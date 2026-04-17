from __future__ import annotations

import logging
import os

from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from app.db.database import SessionLocal
from app.models.spotify_token import SpotifyToken
from app.services.playback_image import get_or_create_image
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)


def _track_id_from_spotify_url(spotify_url: str | None) -> str | None:
    if not spotify_url:
        return None

    cleaned = spotify_url.split("?")[0].rstrip("/")
    if not cleaned:
        return None

    return cleaned.rsplit("/", 1)[-1] or None


def register_playback_handler(dp: Dispatcher) -> None:
    @dp.message(Command("playback"))
    async def playback(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        db = SessionLocal()

        try:
            token = db.query(SpotifyToken).filter_by(user_id=user_id).first()
            if not token:
                await message.answer("Use /login para conectar o Spotify antes de /playback.")
                return

            track = await spotify_service.get_current_or_last_played(db, user_id)
            if not track:
                return

            track_id = _track_id_from_spotify_url(track.get("spotify_url"))
            cover_url = track.get("album_image_url")
            title = str(track.get("track_name") or "")
            artist = str(track.get("artist") or "")
            album = str(track.get("album") or "")

            if not track_id or not cover_url:
                return

            image_path = get_or_create_image(
                track_id=track_id,
                cover_url=str(cover_url),
                title=title,
                artist=artist,
                album=album,
            )

            if image_path is None:
                return

            if not isinstance(image_path, str):
                return

            image_path = image_path.strip()
            if not image_path:
                return

            if not os.path.exists(image_path):
                return

            await message.answer_photo(photo=FSInputFile(image_path))
        except Exception as exc:
            logger.exception("/playback failed", exc_info=exc)
            await message.answer("Não foi possível concluir /playback no momento.")
        finally:
            db.close()
