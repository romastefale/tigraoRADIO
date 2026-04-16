from __future__ import annotations

import base64
import re
from typing import TypedDict
from urllib.parse import urlparse

import httpx

from app.config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

SPOTIFY_TRACK_RE = re.compile(r"^[A-Za-z0-9]{22}$")
DEEZER_TRACK_RE = re.compile(r"^[0-9]+$")


class StreamingTrack(TypedDict):
    service: str
    track_name: str
    artist: str
    artwork_url: str | None


class StreamingService:
    def __init__(self) -> None:
        self._spotify_token: str | None = None

    @staticmethod
    def extract_url(text: str) -> str | None:
        for chunk in text.split():
            if chunk.startswith("http://") or chunk.startswith("https://"):
                return chunk.strip("<>()[]{}.,!\"'")
        return None

    def detect_service(self, url: str) -> str | None:
        host = (urlparse(url).netloc or "").lower()

        if "spotify.com" in host:
            return "Spotify"
        if "deezer.com" in host:
            return "Deezer"
        return None

    def extract_track_id(self, service: str, url: str) -> str | None:
        parsed = urlparse(url)
        chunks = [part for part in parsed.path.split("/") if part]
        if not chunks:
            return None

        if service == "Spotify":
            if "track" not in chunks:
                return None
            idx = chunks.index("track")
            if idx + 1 >= len(chunks):
                return None
            track_id = chunks[idx + 1]
            if SPOTIFY_TRACK_RE.match(track_id):
                return track_id
            return None

        if service == "Deezer":
            if "track" not in chunks:
                return None
            idx = chunks.index("track")
            if idx + 1 >= len(chunks):
                return None
            track_id = chunks[idx + 1]
            if DEEZER_TRACK_RE.match(track_id):
                return track_id

        return None

    async def resolve_track(self, service: str, track_id: str) -> StreamingTrack | None:
        if service == "Spotify":
            return await self._spotify_track(track_id)
        if service == "Deezer":
            return await self._deezer_track(track_id)
        return None

    async def _spotify_track(self, track_id: str) -> StreamingTrack | None:
        token = await self._spotify_access_token()
        if not token:
            return None

        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                f"https://api.spotify.com/v1/tracks/{track_id}",
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code != 200:
            return None

        payload = response.json()
        artists = payload.get("artists") or []
        album = payload.get("album") or {}
        images = album.get("images") or []
        artist_name = artists[0].get("name") if artists else None
        track_name = payload.get("name")

        if not track_name or not artist_name:
            return None

        return {
            "service": "Spotify",
            "track_name": str(track_name),
            "artist": str(artist_name),
            "artwork_url": str(images[0].get("url")) if images else None,
        }

    async def _spotify_access_token(self) -> str | None:
        if self._spotify_token:
            return self._spotify_token

        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            return None

        auth = base64.b64encode(
            f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
        ).decode()

        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )

        if response.status_code != 200:
            return None

        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            return None

        self._spotify_token = str(access_token)
        return self._spotify_token

    async def _deezer_track(self, track_id: str) -> StreamingTrack | None:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(f"https://api.deezer.com/track/{track_id}")

        if response.status_code != 200:
            return None

        payload = response.json()
        if payload.get("error"):
            return None

        title = payload.get("title")
        artist = (payload.get("artist") or {}).get("name")

        if not title or not artist:
            return None

        album = payload.get("album") or {}
        artwork_url = album.get("cover_xl") or album.get("cover_big") or album.get("cover")

        return {
            "service": "Deezer",
            "track_name": str(title),
            "artist": str(artist),
            "artwork_url": str(artwork_url) if artwork_url else None,
        }


streaming_service = StreamingService()
