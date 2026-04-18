from __future__ import annotations

import asyncio
import html
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineQuery,
    InlineQueryResultPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from sqlalchemy.orm import Session

from app.bot.intent import detect_intent
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.core.runtime import allow
from app.db.database import SessionLocal
from app.services.likes import likes_service
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)

bot_dispatcher: Dispatcher | None = None
bot_polling_task: asyncio.Task[None] | None = None
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
BLOCKED_WORDS = ["palavra1", "palavra2"]

# ========================
def _new_session() -> Session:
    return SessionLocal()


async def _handle_spotify_error(message: Message, exc: Exception) -> None:
    logger.exception("Telegram command failed", exc_info=exc)
    await message.answer(
        "Spotify is temporarily unavailable right now. Please try again in a few seconds."
    )


def _telegram_identity(message: Message) -> str:
    user = message.from_user
    if not user:
        return "unknown"

    if user.username:
        return f"@{user.username}"

    return user.full_name


def _format_play_status(track: dict[str, str | None], user_label: str) -> str:
    source = str(track.get("source") or "")
    if source == "current":
        return f"🎹 {html.escape(user_label)} está ouvindo"

    played_at_raw = track.get("played_at")
    formatted_time = "--h--min"
    if isinstance(played_at_raw, str):
        try:
            parsed = datetime.fromisoformat(played_at_raw.replace("Z", "+00:00"))
            formatted_time = parsed.astimezone(SAO_PAULO_TZ).strftime("%Hh%Mmin")
        except ValueError:
            formatted_time = "--h--min"

    return f"🎹 {html.escape(user_label)} ouviu às {formatted_time}"


def _play_caption(
    status_line: str,
    spotify_url: str | None,
    track_name: str,
    artist: str,
) -> str:
    escaped_track = html.escape(track_name)
    if spotify_url:
        track_text = f"<a href=\"{html.escape(spotify_url)}\"><b>{escaped_track}</b></a>"
    else:
        track_text = f"<b>{escaped_track}</b>"

    return (
        f"{status_line}\n"
        f"🎧 {track_text} - "
        f"<i>{html.escape(artist)}</i>"
    )


def _playing_keyboard(track_id: str, total_plays: int, total_likes: int, liked: bool) -> InlineKeyboardMarkup:
    heart = "♥" if liked else "♡"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎵 {total_plays}",
                    callback_data=f"plays:{track_id}",
                ),
                InlineKeyboardButton(
                    text=f"{heart} {total_likes}",
                    callback_data=f"like:{track_id}",
                ),
            ]
        ]
    )


def _register_handlers(dp: Dispatcher) -> None:

    # ========================
    # INLINE MODE
    # ========================
    @dp.inline_query()
    async def inline_play(query: InlineQuery) -> None:
        text = (query.query or "").strip().lower()

        if text != "p":
            return

        user_id = query.from_user.id
        db = _new_session()

        try:
            track = await spotify_service.get_current_or_last_played(db, user_id)
            if not track:
                return

            status_line = _format_play_status(track, query.from_user.full_name)
            caption = _play_caption(
                status_line=status_line,
                spotify_url=track.get("spotify_url"),
                track_name=str(track.get("track_name") or ""),
                artist=str(track.get("artist") or ""),
            )

            album_image_url = track.get("album_image_url")
            if not album_image_url:
                return

            result = InlineQueryResultPhoto(
                id=str(uuid.uuid4()),
                photo_url=album_image_url,
                thumbnail_url=album_image_url,
                caption=caption,
                parse_mode="HTML",
            )

            await query.answer([result], cache_time=1)

        finally:
            db.close()

    # ========================
    # COMMANDS
    # ========================

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        if message.chat.type == "private":
            await message.answer(
                "🎧 Bem-vindo ao Tigrao Radio Bot\n\n"
                "Use /login para conectar seu Spotify\n"
                'e depois use /playing ou "tocando"'
            )
            return

        await message.answer(
            "🎧 Tigrao Radio ativo.\n\n"
            'Use /login no privado e depois use /playing ou "tocando"'
        )

    @dp.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "🎧 Tigrao Radio Bot\n\n"
            "Compartilhe o que você está ouvindo no Spotify.\n\n"
            "Comandos:\n"
            "/login - conectar Spotify\n"
            "/logout - desconectar Spotify\n"
            "/playing - mostrar música atual\n\n"
            "Você também pode usar mensagens:\n"
            '"tocando"\n'
            '"tigraofm"\n'
            '"radinho"\n'
            '"qap"'
        )

    @dp.message(Command("login"))
    async def login(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("🔒 Use /login no privado para conectar seu Spotify.")
            return

        user_id = message.from_user.id if message.from_user else 0
        link = spotify_service.build_auth_url(user_id)
        await message.answer(f"Authorize Spotify access: {link}")

    @dp.message(Command("playing"))
    async def play(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            track = await spotify_service.get_current_or_last_played(db, user_id)
            if not track:
                await message.answer("Nada está tocando agora.")
                return

            track_id = track.get("track_id")
            if not isinstance(track_id, str) or not track_id:
                await message.answer("Não foi possível identificar a música atual.")
                return

            track_url = str(track.get("spotify_url") or "")
            await likes_service.register_play(user_id, track_id)

            total_plays = await likes_service.get_track_play_count(track_id)
            total_likes = await likes_service.get_total_likes(track_id)
            user_total_likes = await likes_service.get_user_total_likes(user_id)
            liked = await likes_service.is_track_liked(user_id, track_id)

            username = _telegram_identity(message)
            if message.from_user and message.from_user.username:
                username = f"@{message.from_user.username}"
                user_link = f"https://t.me/{message.from_user.username}"
            else:
                user_link = f"tg://user?id={user_id}"

            track_name = str(track.get("track_name") or "")
            artist_name = str(track.get("artist") or "")

            track_name = html.escape(track_name)
            artist_name = html.escape(artist_name)
            username = html.escape(username)

            caption = (
                f"🎹 <b><a href=\"{html.escape(user_link)}\">{username}</a></b> está ouvindo… · <i>♥ {user_total_likes}</i>\n\n"
                f"🎧 <b><a href=\"{html.escape(track_url)}\">{track_name}</a></b> — <i>{artist_name}</i>"
            )

            keyboard = _playing_keyboard(track_id, total_plays, total_likes, liked)

            album_image_url = track.get("album_image_url")
            if album_image_url:
                await message.answer_photo(
                    photo=str(album_image_url),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return

            await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)

        except Exception as exc:
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(Command("logout"))
    async def logout(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            await spotify_service.clear_user_session(db, user_id)
            await message.answer(
                "🔌 Desconectado do Spotify.\n"
                "Use /login para conectar novamente."
            )
        except Exception as exc:
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(F.text)
    async def natural_handler(message: Message) -> None:
        if not message.text or not message.from_user:
            return

        text = message.text.strip().lower()

        if text in BLOCKED_WORDS:
            await message.answer("Mensagem não permitida.")
            return

        user_id = message.from_user.id

        if not allow(user_id):
            return

        intent = detect_intent(message.text)
        if intent == "play":
            await play(message)

    @dp.callback_query(F.data.startswith("plays:"))
    async def playing_stats(callback: CallbackQuery) -> None:
        if not callback.data:
            await callback.answer()
            return

        user_id = callback.from_user.id
        track_id = callback.data.split(":", 1)[1]
        user_plays = await likes_service.get_user_play_count(user_id, track_id)
        vez = "vez" if user_plays == 1 else "vezes"
        await callback.answer(
            f"🎶 Você já ouviu {user_plays} {vez}",
            show_alert=True,
        )

    @dp.callback_query(F.data.startswith("like:"))
    async def like_track(callback: CallbackQuery) -> None:
        if not callback.data:
            await callback.answer()
            return

        message = callback.message
        if message is None:
            await callback.answer()
            return

        user_id = callback.from_user.id
        track_id = callback.data.split(":", 1)[1]
        await callback.answer()

        liked = await likes_service.toggle_track_like(user_id, track_id)
        total_likes = await likes_service.get_total_likes(track_id)
        total_plays = await likes_service.get_track_play_count(track_id)

        keyboard = _playing_keyboard(track_id, total_plays, total_likes, liked)
        try:
            await message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass


async def startup_telegram_bot() -> None:
    global bot_dispatcher, bot_polling_task

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN missing; Telegram bot is disabled")
        return

    if bot_polling_task and not bot_polling_task.done():
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN, session=AiohttpSession(timeout=10))
    bot_dispatcher = Dispatcher()
    _register_handlers(bot_dispatcher)

    bot_polling_task = asyncio.create_task(bot_dispatcher.start_polling(bot))


async def shutdown_telegram_bot() -> None:
    global bot_polling_task

    if bot_polling_task is None:
        return

    bot_polling_task.cancel()
    try:
        await bot_polling_task
    except asyncio.CancelledError:
        pass
    finally:
        bot_polling_task = None
