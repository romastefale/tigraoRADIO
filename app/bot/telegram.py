from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from app.bot.intent import detect_intent
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.core.runtime import allow
from app.db.database import SessionLocal
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)

bot_dispatcher: Dispatcher | None = None
bot_polling_task: asyncio.Task[None] | None = None
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
BLOCKED_WORDS = ["palavra1", "palavra2"]


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


def _register_handlers(dp: Dispatcher) -> None:
    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        if message.chat.type == "private":
            await message.answer(
                "🎧 Bem-vindo ao Tigrao Radio Bot\n\n"
                "Use /login para conectar seu Spotify\n"
                'e depois use /play ou "tocando"'
            )
            return

        await message.answer(
            "🎧 Tigrao Radio ativo.\n\n"
            'Use /login no privado e depois use /play ou "tocando"'
        )

    @dp.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "🎧 Tigrao Radio Bot\n\n"
            "Compartilhe o que você está ouvindo no Spotify.\n\n"
            "Comandos:\n"
            "/login - conectar Spotify\n"
            "/logout - desconectar Spotify\n"
            "/play - mostrar música atual\n\n"
            "Você também pode usar mensagens:\n"
            '"tocando"\n'
            '"ouvindo"\n'
            '"qual música"'
        )

    @dp.message(Command("login"))
    async def login(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("🔒 Use /login no privado para conectar seu Spotify.")
            return

        user_id = message.from_user.id if message.from_user else 0
        link = spotify_service.build_auth_url(user_id)
        await message.answer(f"Authorize Spotify access: {link}")

    @dp.message(Command("play"))
    async def play(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            track = await spotify_service.get_current_or_last_played(db, user_id)
            if not track:
                await message.answer("Nada está tocando agora.")
                return

            username = _telegram_identity(message)
            status_line = _format_play_status(track, username)
            caption = _play_caption(
                status_line=status_line,
                spotify_url=track.get("spotify_url"),
                track_name=str(track.get("track_name") or ""),
                artist=str(track.get("artist") or ""),
            )

            album_image_url = track.get("album_image_url")
            if album_image_url:
                await message.answer_photo(
                    photo=str(album_image_url),
                    caption=caption,
                    parse_mode="HTML",
                )
                return

            await message.answer(caption, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(F.text)
    async def natural_handler(message: Message) -> None:
        if not message.text or not message.from_user:
            return

        text = message.text.strip().lower()

        # bloqueio isolado
        if text in BLOCKED_WORDS:
            await message.answer("Mensagem não permitida.")
            return

        user_id = message.from_user.id

        if not allow(user_id):
            return

        intent = detect_intent(message.text)
        if intent == "play":
            await play(message)


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