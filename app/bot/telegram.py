from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.orm import Session

from app.config.settings import TELEGRAM_BOT_TOKEN
from app.db.database import SessionLocal
from app.services.spotify import spotify_service


logger = logging.getLogger(__name__)

bot_dispatcher: Dispatcher | None = None
bot_polling_task: asyncio.Task[None] | None = None


def _format_track(track: dict[str, str | None]) -> str:
    return (
        f"Source: {track.get('source')}\n"
        f"Track: {track.get('track_name')}\n"
        f"Artist: {track.get('artist')}\n"
        f"Album: {track.get('album')}"
    )


def _new_session() -> Session:
    return SessionLocal()


async def _handle_spotify_error(message: Message, exc: Exception) -> None:
    logger.exception("Telegram command failed", exc_info=exc)
    await message.answer(f"Request failed: {exc}")


def _register_handlers(dp: Dispatcher) -> None:
    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(
            "Welcome to Tigrao Radio Bot! Commands: /login /play /album /artist /ranking"
        )

    @dp.message(Command("login"))
    async def login(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        link = spotify_service.build_auth_url(user_id)
        await message.answer(f"Authorize Spotify access: {link}")

    @dp.message(Command("play"))
    async def play(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            track = await spotify_service.get_current_or_last_played(db, user_id)
            await message.answer(_format_track(track))
        except Exception as exc:  # noqa: BLE001
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(Command("album"))
    async def album(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            info = await spotify_service.get_album_info(db, user_id)
            await message.answer(
                f"Album: {info.get('album')}\nArtist: {info.get('artist')}\n"
                f"Track: {info.get('track_name')}\nSource: {info.get('source')}"
            )
        except Exception as exc:  # noqa: BLE001
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(Command("artist"))
    async def artist(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            info = await spotify_service.get_artist_info(db, user_id)
            await message.answer(
                f"Artist: {info.get('artist')}\nTrack: {info.get('track_name')}\n"
                f"Album: {info.get('album')}\nSource: {info.get('source')}"
            )
        except Exception as exc:  # noqa: BLE001
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(Command("ranking"))
    async def ranking(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = _new_session()
        try:
            ranking_info = await spotify_service.get_top_tracks(db, user_id)
            tracks = ranking_info.get("tracks", [])
            if not tracks:
                await message.answer("No top tracks available for this user yet.")
                return

            lines = ["Top tracks:"]
            for idx, track in enumerate(tracks, start=1):
                lines.append(
                    f"{idx}. {track.get('track_name')} — {track.get('artist')} ({track.get('album')})"
                )
            await message.answer("\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            await _handle_spotify_error(message, exc)
        finally:
            db.close()

    @dp.message(F.text)
    async def fallback(message: Message) -> None:
        await message.answer("Unknown command. Use /start to see available commands.")


async def startup_telegram_bot() -> None:
    global bot_dispatcher, bot_polling_task

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN missing; Telegram bot is disabled")
        return

    if bot_polling_task and not bot_polling_task.done():
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
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
