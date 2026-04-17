from __future__ import annotations

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from app.db.database import SessionLocal
from app.models.spotify_token import SpotifyToken
from app.services.playback_image import get_or_create_image
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)
PLAYBACK_TRIGGERS = {"pb", "pty", "strm", "djpidro", "mv", "musicart"}
playback_router = Router(name="playback")


def _track_id_from_spotify_url(spotify_url: str | None) -> str | None:
    if not spotify_url:
        return None

    cleaned = spotify_url.split("?")[0].rstrip("/")
    if not cleaned:
        return None

    return cleaned.rsplit("/", 1)[-1] or None


def _playback_user_label(message: Message) -> str:
    user = message.from_user
    if not user:
        return "unknown"

    if user.username:
        return f"@{user.username}"

    return user.full_name


def is_playback_trigger(text: str) -> bool:
    words = text.lower().split()
    return any(word in PLAYBACK_TRIGGERS for word in words)


async def handle_playback(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    db = SessionLocal()

    try:
        token = db.query(SpotifyToken).filter_by(user_id=user_id).first()
        if not token:
            return

        track = await spotify_service.get_current_or_last_played(db, user_id)
        if not track:
            return

        track_id = _track_id_from_spotify_url(track.get("spotify_url"))
        cover_url = track.get("album_image_url")
        title = str(track.get("track_name") or "")
        artist = str(track.get("artist") or "")
        album = str(track.get("album") or "")

        if not track_id:
            return

        if not cover_url:
            return

        image_path = get_or_create_image(
            track_id=track_id,
            cover_url=str(cover_url),
            title=title,
            artist=artist,
            album=album,
            user_label=_playback_user_label(message),
        )

        if not isinstance(image_path, str):
            return

        if not image_path.strip():
            return

        if not os.path.exists(image_path):
            return

        await message.answer_photo(photo=FSInputFile(image_path))
    except Exception as exc:
        logger.exception("/playback failed", exc_info=exc)
    finally:
        db.close()


@playback_router.message(Command("playback"))
async def playback(message: Message) -> None:
    await handle_playback(message)


@playback_router.message(F.text)
async def playback_trigger(message: Message) -> None:
    if message.text is None:
        return

    if not is_playback_trigger(message.text):
        return

    await handle_playback(message)
