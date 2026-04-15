from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.config.settings import CACHE_TTL_SECONDS, TELEGRAM_BOT_TOKEN, TELEGRAM_RATE_LIMIT_SECONDS
from app.db.database import SessionLocal
from app.services.spotify import (
    build_auth_url,
    get_album_info,
    get_artist_info,
    get_current_or_last_track,
    get_top_tracks,
)
from app.utils.cache import TTLCache
from app.utils.rate_limit import UserRateLimiter

router = Router()
cache = TTLCache(ttl_seconds=CACHE_TTL_SECONDS)
rate_limiter = UserRateLimiter(window_seconds=TELEGRAM_RATE_LIMIT_SECONDS)


async def _with_db(callable_fn):
    def _run():
        with SessionLocal() as db:
            return callable_fn(db)

    return await asyncio.to_thread(_run)


def _is_group(message: Message) -> bool:
    return message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}


def _render_track(track: dict, detailed: bool) -> str:
    if not detailed:
        return f"🎵 {track['track_name']} — {track['artist']}"
    return (
        f"🎵 <b>{track['track_name']}</b>\n"
        f"👤 {track['artist']}\n"
        f"💿 {track['album']}\n"
        f"🖼 {track['album_cover_url'] or '-'}"
    )


def _rate_guard(message: Message) -> bool:
    user = message.from_user
    if user is None:
        return False
    allowed = rate_limiter.allow(user.id)
    return allowed


async def _send_rate_limited(message: Message) -> bool:
    if _rate_guard(message):
        return True
    await message.answer("⏳ Slow down a bit.")
    return False


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not await _send_rate_limited(message):
        return
    compact = _is_group(message)
    if compact:
        await message.answer("👋 Hi! Use /login then /play")
        return
    await message.answer(
        "👋 Hi! I can show your Spotify data.\n"
        "Commands: /login /play /album /artist /ranking"
    )


@router.message(Command("login"))
async def cmd_login(message: Message) -> None:
    if not await _send_rate_limited(message):
        return
    url = build_auth_url()
    await message.answer(f"🔐 Login with Spotify:\n{url}")


@router.message(Command("play"))
async def cmd_play(message: Message) -> None:
    if not await _send_rate_limited(message):
        return

    cache_key = f"play:{message.chat.id}"
    track = cache.get(cache_key)
    if track is None:
        try:
            track = await _with_db(get_current_or_last_track)
        except Exception:
            await message.answer("⚠️ Could not load track.")
            return
        cache.set(cache_key, track)

    await message.answer(_render_track(track, detailed=not _is_group(message)), parse_mode="HTML")


@router.message(Command("album"))
async def cmd_album(message: Message) -> None:
    if not await _send_rate_limited(message):
        return

    cache_key = f"album:{message.chat.id}"
    album = cache.get(cache_key)
    if album is None:
        try:
            album = await _with_db(get_album_info)
        except Exception:
            await message.answer("⚠️ Could not load album.")
            return
        cache.set(cache_key, album)

    if _is_group(message):
        await message.answer(f"💿 {album['album']} — {album['artist']}")
    else:
        await message.answer(
            f"💿 <b>{album['album']}</b>\n👤 {album['artist']}\n🖼 {album['album_cover_url'] or '-'}",
            parse_mode="HTML",
        )


@router.message(Command("artist"))
async def cmd_artist(message: Message) -> None:
    if not await _send_rate_limited(message):
        return

    cache_key = f"artist:{message.chat.id}"
    artist = cache.get(cache_key)
    if artist is None:
        try:
            artist = await _with_db(get_artist_info)
        except Exception:
            await message.answer("⚠️ Could not load artist.")
            return
        cache.set(cache_key, artist)

    if _is_group(message):
        await message.answer(f"👤 {artist['artist']}")
    else:
        genres = ", ".join(artist.get("genres", [])[:3]) or "-"
        await message.answer(
            f"👤 <b>{artist['artist']}</b>\n"
            f"🎼 {genres}\n"
            f"👥 {artist.get('followers') or '-'}\n"
            f"🖼 {artist.get('image_url') or '-'}",
            parse_mode="HTML",
        )


@router.message(Command("ranking"))
async def cmd_ranking(message: Message) -> None:
    if not await _send_rate_limited(message):
        return

    cache_key = f"ranking:{message.chat.id}"
    ranking = cache.get(cache_key)
    if ranking is None:
        try:
            ranking = await _with_db(lambda db: get_top_tracks(db, limit=5))
        except Exception:
            await message.answer("⚠️ Could not load ranking.")
            return
        cache.set(cache_key, ranking)

    if not ranking:
        await message.answer("📭 No top tracks yet.")
        return

    if _is_group(message):
        lines = [f"{idx + 1}. {t['track_name']}" for idx, t in enumerate(ranking)]
    else:
        lines = [f"{idx + 1}. {t['track_name']} — {t['artist']}" for idx, t in enumerate(ranking)]
    await message.answer("🏆 Top tracks\n" + "\n".join(lines))


async def run_bot() -> None:
    if not TELEGRAM_BOT_TOKEN:
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)
