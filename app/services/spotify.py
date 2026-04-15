from __future__ import annotations

import logging
import base64
from typing import Any
from urllib.parse import quote  # ← ADICIONADO

import httpx

from app.config.settings import (
    BASE_URL,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_SCOPES,
)

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self) -> None:
        pass

    async def startup(self) -> None:
        logger.info("Spotify service started.")

    async def shutdown(self) -> None:
        logger.info("Spotify service stopped.")

    def build_auth_url(self, user_id: int) -> str:
        redirect_uri = f"{BASE_URL.rstrip('/')}/callback"

        return (
            "https://accounts.spotify.com/authorize"
            f"?client_id={SPOTIFY_CLIENT_ID}"
            "&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope={quote(SPOTIFY_SCOPES)}"  # ← CORREÇÃO
            f"&state={user_id}"
        )

    def resolve_user_id_from_state(self, state: str) -> int | None:
        try:
            return int(state)
        except Exception:
            return None

    async def exchange_code_for_token(self, db, code: str, user_id: int) -> None:
        redirect_uri = f"{BASE_URL.rstrip('/')}/callback"

        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "Authorization": f"Basic {b64_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        data = response.json()

        logger.info("SPOTIFY TOKEN RESPONSE: %s", data)

    async def get_current_or_last_played(
        self, db, user_id: int
    ) -> dict[str, Any] | None:
        return {
            "source": "last",
            "played_at": None,
            "track_name": "Spotify ainda não configurado",
            "artist": "Sistema",
            "album": "Inicialização",
            "spotify_url": None,
            "album_image_url": None,
        }

    async def clear_user_session(self, db, user_id: int) -> bool:
        logger.info("Clear session called for user_id=%s", user_id)
        return True


spotify_service = SpotifyService()