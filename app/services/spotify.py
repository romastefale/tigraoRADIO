from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import (
    SPOTIFY_CACHE_MAX_ENTRIES,
    SPOTIFY_CACHE_TTL_SECONDS,
    SPOTIFY_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    SPOTIFY_CIRCUIT_BREAKER_THRESHOLD,
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
RETRYABLE_STATUSES = {429, 500, 502, 503}
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 0.5
GLOBAL_HTTP_TIMEOUT_SECONDS = min(SPOTIFY_HTTP_TIMEOUT_SECONDS, 10.0)
STATE_TTL_SECONDS = 900

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    expires_at: float
    payload: dict[str, Any]


class SpotifyService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(SPOTIFY_MAX_CONCURRENT_REQUESTS)
        self._cache: OrderedDict[tuple[int, str], CacheEntry] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._rate_limit: dict[int, deque[float]] = defaultdict(deque)
        self._rate_limit_lock = asyncio.Lock()
        self._breaker_lock = asyncio.Lock()
        self._failure_count = 0
        self._breaker_open_until = 0.0

    async def startup(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(GLOBAL_HTTP_TIMEOUT_SECONDS),
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

    def _build_state(self, user_id: int) -> str:
        payload = {
            "uid": user_id,
            "ts": int(datetime.now(timezone.utc).timestamp()),
        }
        payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
        signature = hmac.new(
            SPOTIFY_CLIENT_SECRET.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature_b64 = base64.urlsafe_b64encode(signature).decode("utf-8").rstrip("=")
        return f"{payload_b64}.{signature_b64}"

    def resolve_user_id_from_state(self, state: str | None) -> int | None:
        if not state or "." not in state:
            return None

        payload_b64, signature_b64 = state.split(".", 1)
        expected_signature = hmac.new(
            SPOTIFY_CLIENT_SECRET.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_signature_b64 = base64.urlsafe_b64encode(expected_signature).decode("utf-8").rstrip("=")

        if not hmac.compare_digest(signature_b64, expected_signature_b64):
            return None

        try:
            padding = "=" * (-len(payload_b64) % 4)
            payload_json = base64.urlsafe_b64decode(payload_b64 + padding)
            payload = json.loads(payload_json)
        except (ValueError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None

        user_id = payload.get("uid")
        issued_at = payload.get("ts")
        if not isinstance(user_id, int) or not isinstance(issued_at, int):
            return None

        now_ts = int(datetime.now(timezone.utc).timestamp())
        if issued_at > now_ts or now_ts - issued_at > STATE_TTL_SECONDS:
            return None

        return user_id

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

        if await self._is_circuit_open():
            logger.warning("Circuit breaker open, skipping Spotify request for user_id=%s url=%s", user_id, url)
            return 503, None

        for attempt in range(MAX_RETRIES + 1):
            if attempt == 0:
                await self._enforce_user_rate_limit(user_id)

            try:
                async with self._semaphore:
                    response = await self._client.request(
                        method=method,
                        url=url,
                        headers=headers,
                        data=data,
                        timeout=httpx.Timeout(GLOBAL_HTTP_TIMEOUT_SECONDS),
                    )
            except httpx.HTTPError as exc:
                logger.exception("Spotify request transport error for user_id=%s url=%s", user_id, url, exc_info=exc)
                await self._record_failure()
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
                if response.status_code < 400:
                    await self._record_success()
                elif response.status_code >= 500:
                    await self._record_failure()
                return response.status_code, payload

            if attempt == MAX_RETRIES:
                await self._record_failure()
                return response.status_code, payload

            retry_after_header = response.headers.get("Retry-After")
            if retry_after_header and retry_after_header.isdigit():
                sleep_seconds = float(retry_after_header)
            else:
                sleep_seconds = BACKOFF_BASE_SECONDS * (2**attempt)
            await asyncio.sleep(sleep_seconds)

        return 500, None

    async def _is_circuit_open(self) -> bool:
        async with self._breaker_lock:
            return monotonic() < self._breaker_open_until

    async def _record_failure(self) -> None:
        async with self._breaker_lock:
            self._failure_count += 1
            if self._failure_count >= SPOTIFY_CIRCUIT_BREAKER_THRESHOLD:
                self._breaker_open_until = monotonic() + SPOTIFY_CIRCUIT_BREAKER_COOLDOWN_SECONDS
                self._failure_count = 0

    async def _record_success(self) -> None:
        async with self._breaker_lock:
            self._failure_count = 0
            self._breaker_open_until = 0.0

    async def _prune_expired_cache_locked(self) -> None:
        now = monotonic()
        expired_keys = [cache_key for cache_key, entry in self._cache.items() if entry.expires_at < now]
        for cache_key in expired_keys:
            self._cache.pop(cache_key, None)

    async def _get_cached(self, user_id: int, key: str) -> dict[str, Any] | None:
        cache_key = (user_id, key)
        async with self._cache_lock:
            await self._prune_expired_cache_locked()
            cached = self._cache.get(cache_key)
            if cached is None:
                return None
            self._cache.move_to_end(cache_key)
            return cached.payload

    async def _set_cache(self, user_id: int, key: str, payload: dict[str, Any]) -> None:
        cache_key = (user_id, key)
        async with self._cache_lock:
            await self._prune_expired_cache_locked()
            self._cache[cache_key] = CacheEntry(
                expires_at=monotonic() + SPOTIFY_CACHE_TTL_SECONDS,
                payload=payload,
            )
            self._cache.move_to_end(cache_key)
            while len(self._cache) > SPOTIFY_CACHE_MAX_ENTRIES:
                self._cache.popitem(last=False)

    async def clear_user_session(self, db: Session, user_id: int) -> bool:
        token_row = db.query(SpotifyToken).filter(SpotifyToken.user_id == user_id).first()
        if token_row is None:
            await self._clear_user_runtime_data(user_id)
            return False

        db.delete(token_row)
        db.commit()
        await self._clear_user_runtime_data(user_id)
        return True

    async def _clear_user_runtime_data(self, user_id: int) -> None:
        async with self._cache_lock:
            cache_keys = [cache_key for cache_key in self._cache if cache_key[0] == user_id]
            for cache_key in cache_keys:
                self._cache.pop(cache_key, None)

        async with self._rate_limit_lock:
            self._rate_limit.pop(user_id, None)

    def build_auth_url(self, user_id: int) -> str:
        if not SPOTIFY_CLIENT_ID:
            raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_ID is not configured")
        if not SPOTIFY_CLIENT_SECRET:
            raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_SECRET is not configured")

        query = urlencode(
            {
                "response_type": "code",
                "client_id": SPOTIFY_CLIENT_ID,
                "scope": SPOTIFY_SCOPES,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
                "state": self._build_state(user_id),
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
        if not payload.get("access_token"):
            raise HTTPException(status_code=400, detail="Spotify token exchange returned invalid access token")

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
            access_token=payload.get("access_token", ""),
            refresh_token=refresh_token,
            expires_in=int(payload.get("expires_in", 3600)),
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
        if not payload.get("access_token"):
            raise HTTPException(status_code=400, detail="Spotify token refresh returned invalid access token")

        new_refresh_token = payload.get("refresh_token", token_row.refresh_token)
        return self._save_token(
            db=db,
            user_id=user_id,
            access_token=payload.get("access_token", token_row.access_token),
            refresh_token=new_refresh_token,
            expires_in=int(payload.get("expires_in", 3600)),
        )

    def _map_track(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item or not isinstance(item, dict):
            return None

        album = item.get("album", {})
        if not isinstance(album, dict):
            album = {}

        artists_source = item.get("artists", [])
        if not isinstance(artists_source, list):
            artists_source = []
        artists = [artist.get("name") for artist in artists_source if isinstance(artist, dict) and artist.get("name")]

        images_source = album.get("images", [])
        images = images_source if isinstance(images_source, list) else []
        highest_res_image = None
        if images:
            sorted_images = sorted(
                (img for img in images if isinstance(img, dict) and img.get("url")),
                key=lambda img: int(img.get("width") or 0),
                reverse=True,
            )
            if sorted_images:
                highest_res_image = sorted_images[0].get("url")

        external_urls = item.get("external_urls", {})
        if not isinstance(external_urls, dict):
            external_urls = {}

        return {
            "track_name": item.get("name"),
            "artist": ", ".join(artists),
            "album": album.get("name"),
            "album_image_url": highest_res_image,
            "spotify_url": external_urls.get("spotify"),
        }

    async def get_current_track(self, db: Session, user_id: int) -> dict[str, Any] | None:
        try:
            token_row = await self.refresh_token_if_needed(db, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh token for current track user_id=%s", user_id, exc_info=exc)
            return None

        status, payload = await self._request_with_retry(
            NOW_PLAYING_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
            user_id=user_id,
        )

        if status == 200 and payload:
            mapped = self._map_track(payload.get("item"))
            if mapped:
                mapped["source"] = "current"
                mapped["played_at"] = None
            return mapped

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
                mapped = self._map_track(payload.get("item"))
                if mapped:
                    mapped["source"] = "current"
                    mapped["played_at"] = None
                return mapped

        logger.warning("Current track lookup failed for user_id=%s status=%s payload=%s", user_id, status, payload)
        return None

    async def get_last_played_track(self, db: Session, user_id: int) -> dict[str, Any] | None:
        try:
            token_row = await self.refresh_token_if_needed(db, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to refresh token for recent track user_id=%s", user_id, exc_info=exc)
            return None

        status, payload = await self._request_with_retry(
            RECENTLY_PLAYED_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
            user_id=user_id,
        )

        if status == 200 and payload:
            items = payload.get("items", []) if isinstance(payload, dict) else []
            if not items:
                return None
            first_item = items[0] if isinstance(items[0], dict) else {}
            mapped = self._map_track(first_item.get("track"))
            if mapped:
                mapped["source"] = "last"
                mapped["played_at"] = first_item.get("played_at")
            return mapped

        logger.warning("Recently played lookup failed for user_id=%s status=%s payload=%s", user_id, status, payload)
        return None

    async def get_current_or_last_played(self, db: Session, user_id: int) -> dict[str, Any] | None:
        cache_key = "play"
        cached = await self._get_cached(user_id, cache_key)
        if cached:
            return cached

        try:
            current = await self.get_current_track(db, user_id)
            if current:
                await self._set_cache(user_id, cache_key, current)
                return current

            last_played = await self.get_last_played_track(db, user_id)
            if last_played:
                await self._set_cache(user_id, cache_key, last_played)
                return last_played
        except Exception as exc:  # noqa: BLE001
            logger.exception("Track lookup failed for user_id=%s", user_id, exc_info=exc)

        return None


spotify_service = SpotifyService()
