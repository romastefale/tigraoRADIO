from __future__ import annotations

import html
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, InlineQuery, InlineQueryResultPhoto, Message
from sqlalchemy.orm import Session

from app.bot.intent import detect_intent
from app.bot.playback import playback_router
from app.config.settings import BASE_URL, TELEGRAM_BOT_TOKEN
from app.core.runtime import allow
from app.db.database import SessionLocal
from app.render.renderer import render_image
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)

SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
BLOCKED_WORDS = ["palavra1", "palavra2"]

# Webhook mode: dispatcher must exist at import time
bot_dispatcher = Dispatcher()
bot: Bot | None = None
_handlers_registered = False


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
    global _handlers_registered
    if _handlers_registered:
        return

    # Inline mode
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
                photo_url=str(album_image_url),
                thumbnail_url=str(album_image_url),
                caption=caption,
                parse_mode="HTML",
            )

            await query.answer([result], cache_time=1)

        finally:
            db.close()

    # Commands
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
            "/play - mostrar música atual\n"
            "/playback - gerar imagem da música atual\n\n"
            "Você também pode usar mensagens:\n"
            '"tocando"\n'
            '"tigraofm"\n'
            '"radinho"\n'
            '"qap"\n'
            '"pb"\n'
            '"pty"\n'
            '"strm"\n'
            '"djpidro"\n'
            '"mv"\n'
            '"musicart"'
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

        except Exception as exc:
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(Command("playimg"))
    async def playimg(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            track = await spotify_service.get_current_or_last_played(db, user_id)
            if not track:
                raise RuntimeError("Missing track data")

            cover_url = str(track.get("album_image_url") or "").strip()
            if not cover_url:
                raise RuntimeError("Missing cover url")

            payload = {
                "USER": _telegram_identity(message),
                "TRACK": str(track.get("track_name") or ""),
                "ARTIST": str(track.get("artist") or ""),
                "ALBUM": str(track.get("album") or ""),
                "COVER": cover_url,
            }

            image_bytes = await render_image(payload)
            if not image_bytes:
                raise RuntimeError("Empty image bytes")

            await message.answer_photo(
                photo=BufferedInputFile(image_bytes, filename="playimg.png")
            )
        except Exception as e:
            await message.answer(str(e))
            await play(message)
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

    # Playback router before generic natural-text handler
    dp.include_router(playback_router)

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
        if intent == "playimg":
            await playimg(message)

    _handlers_registered = True


async def startup_telegram_bot() -> None:
    global bot

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN missing; Telegram bot is disabled")
        return

    if bot is None:
        bot = Bot(token=TELEGRAM_BOT_TOKEN, session=AiohttpSession(timeout=10))

    _register_handlers(bot_dispatcher)

    if BASE_URL:
        webhook_url = f"{BASE_URL.rstrip('/')}/webhook"
        await bot.set_webhook(webhook_url)
        logger.info("Webhook configured: %s", webhook_url)
    else:
        logger.warning("BASE_URL missing; webhook was not configured")


async def shutdown_telegram_bot() -> None:
    global bot

    if bot is None:
        return

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception:
        logger.exception("Failed to delete webhook on shutdown")

    await bot.session.close()
    bot = None
