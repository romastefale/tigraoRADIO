from __future__ import annotations

import asyncio
import html
import logging
import uuid
from collections import deque
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message, InlineQuery, InlineQueryResultPhoto
from sqlalchemy.orm import Session

from app.bot.intent import detect_intent
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.core.runtime import allow
from app.db.database import SessionLocal
from app.services.streaming import streaming_service
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)

bot_dispatcher: Dispatcher | None = None
bot_polling_task: asyncio.Task[None] | None = None
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
BLOCKED_WORDS = ["palavra1", "palavra2"]
STREAMING_RATE_WINDOW_SECONDS = 10.0
STREAMING_RATE_LIMIT = 5
STREAMING_CACHE_TTL_SECONDS = 180.0
STREAMING_DEDUP_TTL_SECONDS = 120.0

_streaming_rate_limit: dict[int, deque[float]] = {}
_streaming_result_cache: dict[str, tuple[float, dict[str, str | None]]] = {}
_streaming_seen_messages: dict[tuple[int, int], float] = {}

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


def _streaming_prune_state() -> None:
    now = monotonic()

    expired_seen = [
        key
        for key, seen_at in _streaming_seen_messages.items()
        if now - seen_at > STREAMING_DEDUP_TTL_SECONDS
    ]
    for key in expired_seen:
        _streaming_seen_messages.pop(key, None)

    expired_cache = [
        key
        for key, (cached_at, _) in _streaming_result_cache.items()
        if now - cached_at > STREAMING_CACHE_TTL_SECONDS
    ]
    for key in expired_cache:
        _streaming_result_cache.pop(key, None)

    for chat_id, timestamps in list(_streaming_rate_limit.items()):
        while timestamps and now - timestamps[0] > STREAMING_RATE_WINDOW_SECONDS:
            timestamps.popleft()
        if not timestamps:
            _streaming_rate_limit.pop(chat_id, None)


def _streaming_should_rate_limit(chat_id: int) -> bool:
    now = monotonic()
    history = _streaming_rate_limit.setdefault(chat_id, deque())
    while history and now - history[0] > STREAMING_RATE_WINDOW_SECONDS:
        history.popleft()

    if len(history) >= STREAMING_RATE_LIMIT:
        return True

    history.append(now)
    return False


def _streaming_identity(message: Message) -> str:
    if not message.from_user:
        return "usuário"
    if message.from_user.username:
        return f"@{message.from_user.username}"
    name = message.from_user.full_name.strip()
    return name or "usuário"


def _streaming_response_text(
    user_label: str,
    service: str,
    track_name: str,
    artist: str,
) -> str:
    return (
        f"🎹 {html.escape(user_label)} está ouvindo no {html.escape(service)}\n"
        f"🎧 {html.escape(track_name)} - {html.escape(artist)}"
    )


async def _process_streaming_message(
    message: Message,
    *,
    link_text: str | None,
    private_missing_link_message: bool,
) -> None:
    if not message.chat or not message.chat.id:
        return

    if not message.message_id:
        return

    chat_id = int(message.chat.id)
    chat_type = str(message.chat.type or "")

    _streaming_prune_state()

    dedup_key = (chat_id, int(message.message_id))
    if dedup_key in _streaming_seen_messages:
        return
    _streaming_seen_messages[dedup_key] = monotonic()

    if _streaming_should_rate_limit(chat_id):
        return

    if not link_text:
        if private_missing_link_message and chat_type == "private":
            await message.bot.send_message(
                chat_id=chat_id,
                text="Envie um link válido de Apple Music, Spotify, Deezer ou YouTube Music.",
            )
        return

    if "http" not in link_text.lower():
        return

    url = streaming_service.extract_url(link_text)
    if not url:
        return

    service = streaming_service.detect_service(url)
    if not service:
        return

    track_id: str | None = None
    cache_key: str
    if service in {"Spotify", "Deezer"}:
        track_id = streaming_service.extract_track_id(service, url)
        if not track_id:
            return
        cache_key = f"{service}:{track_id}"
    else:
        cache_key = f"{service}:{url}"

    now = monotonic()
    cached = _streaming_result_cache.get(cache_key)
    if cached and now - cached[0] <= STREAMING_CACHE_TTL_SECONDS:
        track_data = cached[1]
    else:
        try:
            if track_id:
                resolved = await asyncio.wait_for(
                    streaming_service.resolve_track(service, track_id), timeout=4.5
                )
            else:
                resolved = await asyncio.wait_for(
                    streaming_service.resolve_indirect_track(service, url), timeout=4.5
                )
        except Exception:
            return

        if not resolved:
            return

        track_name = str(resolved.get("track_name") or "").strip()
        artist = str(resolved.get("artist") or "").strip()
        if not track_name or not artist:
            return

        track_data = {
            "service": str(resolved.get("service") or service),
            "track_name": track_name,
            "artist": artist,
            "artwork_url": str(resolved.get("artwork_url") or "") or None,
        }
        _streaming_result_cache[cache_key] = (now, track_data)

    text = _streaming_response_text(
        user_label=_streaming_identity(message),
        service=str(track_data.get("service") or service),
        track_name=str(track_data.get("track_name") or ""),
        artist=str(track_data.get("artist") or ""),
    )

    artwork_url = track_data.get("artwork_url")
    if artwork_url:
        try:
            await message.bot.send_photo(
                chat_id=chat_id,
                photo=str(artwork_url),
                caption=text,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    await message.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


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

    @dp.message(Command("streaming"))
    async def streaming_command(message: Message) -> None:
        command_text = message.text or ""
        pieces = command_text.split(maxsplit=1)
        link_text = pieces[1] if len(pieces) > 1 else None
        await _process_streaming_message(
            message,
            link_text=link_text,
            private_missing_link_message=True,
        )

    @dp.message(F.text.contains("http"))
    async def streaming_link_listener(message: Message) -> None:
        text = message.text or ""
        if "http" not in text.lower():
            return

        await _process_streaming_message(
            message,
            link_text=text,
            private_missing_link_message=False,
        )

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
