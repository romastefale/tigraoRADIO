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
from app.db.database import SessionLocal

from app.models.spotify_token import SpotifyToken

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self) -> None:
        pass

    async def startup(self) -> None:
        logger.info("Spotify service started.")

    async def shutdown(self) -> None:
        logger.info("Spotify service stopped.")

    async def _get_app_access_token(self) -> str | None:
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://accounts.spotify.com/api/token",
                    data={"grant_type": "client_credentials"},
                    headers={
                        "Authorization": f"Basic {b64_auth}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
        except Exception:
            logger.exception("Failed to request Spotify app access token")
            return None

        if response.status_code != 200:
            logger.error("Spotify app token request failed: %s", response.text)
            return None

        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            logger.error("Spotify app token response missing access_token: %s", data)
            return None

        return str(access_token)

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
        except ValueError:
            return None

    async def exchange_code_for_token(self, code: str, user_id: int) -> None:
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

        with SessionLocal() as db:
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

    async def _refresh_token(self, user_id: int) -> SpotifyToken | None:
        with SessionLocal() as db:
            token = db.query(SpotifyToken).filter_by(user_id=user_id).first()
            if not token:
                return None
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
            db.refresh(token)

            return token

    async def get_current_or_last_played(self, user_id: int) -> dict[str, Any] | None:

        with SessionLocal() as db:
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
            refreshed = await self._refresh_token(user_id)
            if refreshed:
                response = await fetch_current(refreshed.access_token)

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
            refreshed = await self._refresh_token(user_id)
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

    async def clear_user_session(self, user_id: int) -> bool:
        with SessionLocal() as db:
            token = db.query(SpotifyToken).filter_by(user_id=user_id).first()
            if token:
                db.delete(token)
                db.commit()

        return True

    async def get_audio_features(self, track_id: str) -> dict[str, float] | None:
        normalized_track_id = track_id.strip() if isinstance(track_id, str) else ""
        if not normalized_track_id:
            return None

        access_token = await self._get_app_access_token()
        if not access_token:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"https://api.spotify.com/v1/audio-features/{normalized_track_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except Exception:
            logger.exception("Failed to request audio features for track_id=%s", normalized_track_id)
            return None

        if response.status_code != 200:
            logger.error("Spotify audio features request failed for %s: %s", normalized_track_id, response.text)
            return None

        data = response.json()
        try:
            return {
                "valence": float(data["valence"]),
                "energy": float(data["energy"]),
                "danceability": float(data["danceability"]),
            }
        except (KeyError, TypeError, ValueError):
            logger.error("Spotify audio features payload invalid for %s: %s", normalized_track_id, data)
            return None


spotify_service = SpotifyService()
