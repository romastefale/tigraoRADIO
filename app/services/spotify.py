from __future__ import annotations

import logging
import base64
from typing import Any
from urllib.parse import quote
from datetime import datetime, timedelta

import httpx

from app.config.settings import (
    BASE_URL,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_SCOPES,
)

from app.models.spotify_token import SpotifyToken

logger = logging.getLogger(__name__)

# 🔥 IMPORTANTE: tem que ser EXATAMENTE igual ao Spotify Dashboard
REDIRECT_URI = "https://tigraoradio-production.up.railway.app/callback"


class SpotifyService:
    def __init__(self) -> None:
        pass

    async def startup(self) -> None:
        logger.info("Spotify service started.")

    async def shutdown(self) -> None:
        logger.info("Spotify service stopped.")

    def build_auth_url(self, user_id: int) -> str:
        return (
            "https://accounts.spotify.com/authorize"
            f"?client_id={SPOTIFY_CLIENT_ID}"
            "&response_type=code"
            f"&redirect_uri={REDIRECT_URI}"
            f"&scope={quote(SPOTIFY_SCOPES)}"
            f"&state={user_id}"
        )

    def resolve_user_id_from_state(self, state: str) -> int | None:
        try:
            return int(state)
        except Exception:
            return None

    async def exchange_code_for_token(self, db, code: str, user_id: int) -> None:
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                },
                headers={
                    "Authorization": f"Basic {b64_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        data = response.json()

        logger.error("SPOTIFY STATUS: %s", response.status_code)
        logger.error("SPOTIFY RESPONSE: %s", data)

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")

        if not access_token or not refresh_token or not expires_in:
            logger.error("Invalid token response")
            return

        expiration = datetime.utcnow() + timedelta(seconds=expires_in)

        existing = db.query(SpotifyToken).filter_by(user_id=user_id).first()

        if existing:
            existing.access_token = access_token
            existing.refresh_token = refresh_token
            existing.expiration = expiration
        else:
            token = SpotifyToken(
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expiration=expiration,
            )
            db.add(token)

        db.commit()

        logger.info("Spotify token saved for user_id=%s", user_id)

    async def get_current_or_last_played(
        self, db, user_id: int
    ) -> dict[str, Any] | None:

        token = db.query(SpotifyToken).filter_by(user_id=user_id).first()

        if not token:
            return {
                "source": "last",
                "played_at": None,
                "track_name": "Faça /login para conectar seu Spotify",
                "artist": "Spotify",
                "album": "",
                "spotify_url": None,
                "album_image_url": None,
            }

        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.spotify.com/v1/me/player/currently-playing",
                headers={"Authorization": f"Bearer {token.access_token}"},
            )

        if response.status_code != 200:
            return {
                "source": "last",
                "played_at": None,
                "track_name": "Nada está tocando agora",
                "artist": "Spotify",
                "album": "",
                "spotify_url": None,
                "album_image_url": None,
            }

        data = response.json()
        item = data.get("item")

        if not item:
            return None

        return {
            "source": "current",
            "played_at": None,
            "track_name": item["name"],
            "artist": item["artists"][0]["name"],
            "album": item["album"]["name"],
            "spotify_url": item["external_urls"]["spotify"],
            "album_image_url": item["album"]["images"][0]["url"],
        }

    async def clear_user_session(self, db, user_id: int) -> bool:
        token = db.query(SpotifyToken).filter_by(user_id=user_id).first()

        if token:
            db.delete(token)
            db.commit()

        return True


spotify_service = SpotifyService()