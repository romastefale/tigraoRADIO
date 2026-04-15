from __future__ import annotations

import asyncio
import base64
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import (
    SPOTIFY_CACHE_TTL_SECONDS,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_HTTP_TIMEOUT_SECONDS,
    SPOTIFY_MAX_CONCURRENT_REQUESTS,
    SPOTIFY_PER_USER_RATE_LIMIT,
    SPOTIFY_RATE_LIMIT_WINDOW_SECONDS,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES,
)
from app.models.spotify_token import SpotifyToken


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
NOW_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played?limit=1"
TOP_TRACKS_URL = "https://api.spotify.com/v1/me/top/tracks?limit=5"
RETRYABLE_STATUSES = {429, 500, 502, 503}
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 0.5

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    expires_at: float
    payload: dict[str, Any]


class SpotifyService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(SPOTIFY_MAX_CONCURRENT_REQUESTS)
        self._cache: dict[tuple[int, str], CacheEntry] = {}
        self._cache_lock = asyncio.Lock()
        self._rate_limit: dict[int, deque[float]] = defaultdict(deque)
        self._rate_limit_lock = asyncio.Lock()

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(SPOTIFY_HTTP_TIMEOUT_SECONDS),
                limits=httpx.Limits(
                    max_connections=SPOTIFY_MAX_CONCURRENT_REQUESTS,
                    max_keepalive_connections=max(5, SPOTIFY_MAX_CONCURRENT_REQUESTS // 2),
                ),
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _basic_auth_header(self) -> str:
        creds = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")
        return "Basic " + base64.b64encode(creds).decode("utf-8")

    async def _enforce_user_rate_limit(self, user_id: int) -> None:
        now = monotonic()
        async with self._rate_limit_lock:
            bucket = self._rate_limit[user_id]
            while bucket and now - bucket[0] > SPOTIFY_RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()

            if len(bucket) >= SPOTIFY_PER_USER_RATE_LIMIT:
                raise HTTPException(
                    status_code=429,
                    detail="Too many Spotify requests for this user. Please retry shortly.",
                )

            bucket.append(now)

    async def _request_with_retry(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        user_id: int,
    ) -> tuple[int, dict[str, Any] | None]:
        await self.startup()
        assert self._client is not None

        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0:
                await self._enforce_user_rate_limit(user_id)

            try:
                async with self._semaphore:
                    response = await self._client.request(method=method, url=url, headers=headers, data=data)
            except httpx.HTTPError as exc:
                logger.exception("Spotify request transport error for user_id=%s url=%s", user_id, url, exc_info=exc)
                if attempt == MAX_RETRIES:
                    return 503, None
                await asyncio.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
                continue

            payload: dict[str, Any] | None
            try:
                payload = response.json() if response.text else None
            except ValueError:
                payload = None

            if response.status_code not in RETRYABLE_STATUSES:
                return response.status_code, payload

            if attempt == MAX_RETRIES:
                return response.status_code, payload

            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header and retry_after_header.isdigit():
                sleep_seconds = float(retry_after_header)
            else:
                sleep_seconds = BACKOFF_BASE_SECONDS * (2**attempt)
            await asyncio.sleep(sleep_seconds)

        return 500, None

    async def _get_cached(self, user_id: int, key: str) -> dict[str, Any] | None:
        cache_key = (user_id, key)
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            if cached.expires_at < monotonic():
                self._cache.pop(cache_key, None)
                return None
            return cached.payload

    async def _set_cache(self, user_id: int, key: str, payload: dict[str, Any]) -> None:
        cache_key = (user_id, key)
        async with self._cache_lock:
            self._cache[cache_key] = CacheEntry(
                expires_at=monotonic() + SPOTIFY_CACHE_TTL_SECONDS,
                payload=payload,
            )

    def build_auth_url(self, user_id: int) -> str:
        if not SPOTIFY_CLIENT_ID:
            raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_ID is not configured")

        query = urlencode(
            {
                "response_type": "code",
                "client_id": SPOTIFY_CLIENT_ID,
                "scope": SPOTIFY_SCOPES,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
                "state": str(user_id),
            }
        )
        return f"{AUTH_URL}?{query}"

    def _save_token(
        self,
        db: Session,
        user_id: int,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> SpotifyToken:
        expiration = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        token_row = db.query(SpotifyToken).filter(SpotifyToken.user_id == user_id).first()

        if token_row is None:
            token_row = SpotifyToken(
                user_id=user_id,
                access_token=access_token,
                refresh_token=refresh_token,
                expiration=expiration,
            )
            db.add(token_row)
        else:
            token_row.access_token = access_token
            token_row.refresh_token = refresh_token
            token_row.expiration = expiration

        db.commit()
        db.refresh(token_row)
        return token_row

    async def exchange_code_for_token(self, db: Session, code: str, user_id: int) -> SpotifyToken:
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            raise HTTPException(status_code=500, detail="Spotify client credentials are not configured")

        status, payload = await self._request_with_retry(
            TOKEN_URL,
            method="POST",
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
            },
            user_id=user_id,
        )

        if status != 200 or payload is None:
            raise HTTPException(status_code=400, detail=f"Spotify token exchange failed: {payload}")

        refresh_token = payload.get("refresh_token")
        if not refresh_token:
            existing = db.query(SpotifyToken).filter(SpotifyToken.user_id == user_id).first()
            if existing:
                refresh_token = existing.refresh_token

        if not refresh_token:
            raise HTTPException(status_code=400, detail="Spotify token exchange did not return refresh token")

        return self._save_token(
            db=db,
            user_id=user_id,
            access_token=payload["access_token"],
            refresh_token=refresh_token,
            expires_in=payload["expires_in"],
        )

    async def refresh_token_if_needed(self, db: Session, user_id: int) -> SpotifyToken:
        token_row = db.query(SpotifyToken).filter(SpotifyToken.user_id == user_id).first()
        if token_row is None:
            raise HTTPException(status_code=404, detail="No Spotify token found. Complete OAuth login first.")

        now_utc = datetime.now(timezone.utc)
        expiration_utc = token_row.expiration.replace(tzinfo=timezone.utc)

        if expiration_utc > now_utc + timedelta(seconds=30):
            return token_row

        status, payload = await self._request_with_retry(
            TOKEN_URL,
            method="POST",
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_row.refresh_token,
            },
            user_id=user_id,
        )

        if status != 200 or payload is None:
            raise HTTPException(status_code=400, detail=f"Spotify token refresh failed: {payload}")

        new_refresh_token = payload.get("refresh_token", token_row.refresh_token)
        return self._save_token(
            db=db,
            user_id=user_id,
            access_token=payload["access_token"],
            refresh_token=new_refresh_token,
            expires_in=payload["expires_in"],
        )

    def _map_track(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item:
            return None

        album = item.get("album", {})
        artists = [artist.get("name") for artist in item.get("artists", []) if artist.get("name")]
        images = album.get("images", [])

        return {
            "track_name": item.get("name"),
            "artist": ", ".join(artists),
            "album": album.get("name"),
            "album_cover_url": images[0].get("url") if images else None,
        }

    async def get_current_track(self, db: Session, user_id: int) -> dict[str, Any] | None:
        token_row = await self.refresh_token_if_needed(db, user_id)

        status, payload = await self._request_with_retry(
            NOW_PLAYING_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
            user_id=user_id,
        )

        if status == 200 and payload:
            return self._map_track(payload.get("item"))

        if status in (204, 404):
            return None

        if status == 401:
            token_row = await self.refresh_token_if_needed(db, user_id)
            status, payload = await self._request_with_retry(
                NOW_PLAYING_URL,
                headers={"Authorization": f"Bearer {token_row.access_token}"},
                user_id=user_id,
            )
            if status == 200 and payload:
                return self._map_track(payload.get("item"))

        raise HTTPException(status_code=400, detail=f"Spotify current track lookup failed: {payload}")

    async def get_last_played_track(self, db: Session, user_id: int) -> dict[str, Any] | None:
        token_row = await self.refresh_token_if_needed(db, user_id)

        status, payload = await self._request_with_retry(
            RECENTLY_PLAYED_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
            user_id=user_id,
        )

        if status == 200 and payload:
            items = payload.get("items", [])
            if not items:
                return None
            return self._map_track(items[0].get("track"))

        raise HTTPException(status_code=400, detail=f"Spotify recently played lookup failed: {payload}")

    async def get_current_or_last_played(self, db: Session, user_id: int) -> dict[str, Any]:
        cache_key = "play"
        cached = await self._get_cached(user_id, cache_key)
        if cached:
            return cached

        current = await self.get_current_track(db, user_id)
        if current:
            result = {"source": "currently_playing", **current}
            await self._set_cache(user_id, cache_key, result)
            return result

        last_played = await self.get_last_played_track(db, user_id)
        if last_played:
            result = {"source": "recently_played", **last_played}
            await self._set_cache(user_id, cache_key, result)
            return result

        raise HTTPException(status_code=404, detail="No current or recently played track found")

    async def get_album_info(self, db: Session, user_id: int) -> dict[str, Any]:
        cache_key = "album"
        cached = await self._get_cached(user_id, cache_key)
        if cached:
            return cached

        track = await self.get_current_or_last_played(db, user_id)
        payload = {
            "album": track.get("album"),
            "artist": track.get("artist"),
            "track_name": track.get("track_name"),
            "album_cover_url": track.get("album_cover_url"),
            "source": track.get("source"),
        }
        await self._set_cache(user_id, cache_key, payload)
        return payload

    async def get_artist_info(self, db: Session, user_id: int) -> dict[str, Any]:
        cache_key = "artist"
        cached = await self._get_cached(user_id, cache_key)
        if cached:
            return cached

        track = await self.get_current_or_last_played(db, user_id)
        payload = {
            "artist": track.get("artist"),
            "track_name": track.get("track_name"),
            "album": track.get("album"),
            "source": track.get("source"),
        }
        await self._set_cache(user_id, cache_key, payload)
        return payload

    async def get_top_tracks(self, db: Session, user_id: int) -> dict[str, Any]:
        cache_key = "ranking"
        cached = await self._get_cached(user_id, cache_key)
        if cached:
            return cached

        token_row = await self.refresh_token_if_needed(db, user_id)
        status, payload = await self._request_with_retry(
            TOP_TRACKS_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
            user_id=user_id,
        )

        if status != 200 or payload is None:
            raise HTTPException(status_code=400, detail=f"Spotify top tracks lookup failed: {payload}")

        tracks: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            artists = [artist.get("name") for artist in item.get("artists", []) if artist.get("name")]
            tracks.append(
                {
                    "track_name": item.get("name"),
                    "artist": ", ".join(artists),
                    "album": item.get("album", {}).get("name"),
                }
            )

        result = {"tracks": tracks}
        await self._set_cache(user_id, cache_key, result)
        return result


spotify_service = SpotifyService()
