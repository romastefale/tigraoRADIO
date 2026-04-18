from __future__ import annotations

import logging
import base64
from typing import Any
from urllib.parse import quote
from datetime import datetime, timedelta

import httpx

from app.config.settings import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_SCOPES,
    SPOTIFY_REDIRECT_URI,
)

from app.models.spotify_token import SpotifyToken

logger = logging.getLogger(__name__)


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
            f"&redirect_uri={SPOTIFY_REDIRECT_URI}"
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
                    "redirect_uri": SPOTIFY_REDIRECT_URI,
                },
                headers={
                    "Authorization": f"Basic {b64_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        data = response.json()

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")

        if not access_token or not expires_in:
            logger.error("Invalid token response: %s", data)
            return

        expiration = datetime.utcnow() + timedelta(seconds=expires_in)

        existing = db.query(SpotifyToken).filter_by(user_id=user_id).first()

        if existing:
            existing.access_token = access_token
            existing.expiration = expiration

            # 🔥 NÃO perder refresh_token antigo
            if refresh_token:
                existing.refresh_token = refresh_token
        else:
            db.add(
                SpotifyToken(
                    user_id=user_id,
                    access_token=access_token,
                    refresh_token=refresh_token or "",
                    expiration=expiration,
                )
            )

        db.commit()

    async def _refresh_token(self, db, token: SpotifyToken) -> SpotifyToken | None:
        if not token.refresh_token:
            logger.error("Missing refresh token for user_id=%s", token.user_id)
            return None

        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                },
                headers={
                    "Authorization": f"Basic {b64_auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        data = response.json()

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")

        if not access_token or not expires_in:
            logger.error("Refresh failed: %s", data)
            return None

        token.access_token = access_token
        token.expiration = datetime.utcnow() + timedelta(seconds=expires_in)

        db.commit()

        return token

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
                "track_id": None,
                "spotify_url": None,
                "album_image_url": None,
            }

        async def fetch_current(access_token: str):
            async with httpx.AsyncClient() as client:
                return await client.get(
                    "https://api.spotify.com/v1/me/player/currently-playing",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

        async def fetch_recent(access_token: str):
            async with httpx.AsyncClient() as client:
                return await client.get(
                    "https://api.spotify.com/v1/me/player/recently-played?limit=1",
                    headers={"Authorization": f"Bearer {access_token}"},
                )

        # 🔥 tentativa com token atual
        response = await fetch_current(token.access_token)

        # 🔥 se token inválido → refresh e tenta de novo
        if response.status_code == 401:
            refreshed = await self._refresh_token(db, token)
            if refreshed:
                response = await fetch_current(refreshed.access_token)
                token = refreshed

        if response.status_code == 200:
            data = response.json()
            item = data.get("item")

            if item:
                return {
                    "source": "current",
                    "played_at": None,
                    "track_name": item["name"],
                    "artist": item["artists"][0]["name"],
                    "album": item["album"]["name"],
                    "track_id": item.get("id"),
                    "spotify_url": item["external_urls"]["spotify"],
                    "album_image_url": item["album"]["images"][0]["url"],
                }

        # 🔥 fallback
        recent = await fetch_recent(token.access_token)

        # 🔥 novamente tratar 401
        if recent.status_code == 401:
            refreshed = await self._refresh_token(db, token)
            if refreshed:
                recent = await fetch_recent(refreshed.access_token)

        if recent.status_code != 200:
            logger.error("Spotify fallback error: %s", recent.text)
            return {
                "source": "last",
                "played_at": None,
                "track_name": "Erro ao acessar Spotify",
                "artist": "Spotify",
                "album": "",
                "track_id": None,
                "spotify_url": None,
                "album_image_url": None,
            }

        data = recent.json()
        items = data.get("items")

        if not items:
            return {
                "source": "last",
                "played_at": None,
                "track_name": "Nada encontrado",
                "artist": "Spotify",
                "album": "",
                "track_id": None,
                "spotify_url": None,
                "album_image_url": None,
            }

        track = items[0]["track"]

        return {
            "source": "last",
            "played_at": items[0].get("played_at"),
            "track_name": track["name"],
            "artist": track["artists"][0]["name"],
            "album": track["album"]["name"],
            "track_id": track.get("id"),
            "spotify_url": track["external_urls"]["spotify"],
            "album_image_url": track["album"]["images"][0]["url"],
        }

    async def clear_user_session(self, db, user_id: int) -> bool:
        token = db.query(SpotifyToken).filter_by(user_id=user_id).first()

        if token:
            db.delete(token)
            db.commit()

        return True


spotify_service = SpotifyService()