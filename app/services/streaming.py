from __future__ import annotations

import asyncio
import base64
import html
import re
import unicodedata
from difflib import SequenceMatcher
from typing import TypedDict
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

SPOTIFY_TRACK_RE = re.compile(r"^[A-Za-z0-9]{22}$")
DEEZER_TRACK_RE = re.compile(r"^[0-9]+$")
META_TAG_RE = re.compile(
    r"<meta\s+[^>]*(?:property|name)=[\"'](?P<key>[^\"']+)[\"'][^>]*content=[\"'](?P<value>[^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)


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
                cleaned = chunk.strip("<>()[]{}.,!\"'")
                return StreamingService._normalize_url_for_cache(cleaned)
        return None

    @staticmethod
    def _normalize_url_for_cache(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]

        normalized_query: str = ""
        if "music.youtube.com" in host:
            query_values = parse_qs(parsed.query, keep_blank_values=False)
            allowed_keys = ("v", "list", "title", "artist")
            filtered_pairs: list[tuple[str, str]] = []
            for key in allowed_keys:
                for value in query_values.get(key, []):
                    value = value.strip()
                    if value:
                        filtered_pairs.append((key, value))
            normalized_query = urlencode(filtered_pairs)
        elif "music.apple.com" in host:
            query_values = parse_qs(parsed.query, keep_blank_values=False)
            filtered_pairs = [
                (key, value.strip())
                for key, values in sorted(query_values.items())
                for value in values
                if value and value.strip() and key == "i"
            ]
            normalized_query = urlencode(filtered_pairs)

        return parsed._replace(netloc=host, fragment="", query=normalized_query).geturl()

    def detect_service(self, url: str) -> str | None:
        host = (urlparse(url).netloc or "").lower()

        if "spotify.com" in host:
            return "Spotify"
        if "deezer.com" in host:
            return "Deezer"
        if "music.apple.com" in host:
            return "Apple Music"
        if "music.youtube.com" in host:
            return "YouTube Music"
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

    async def resolve_indirect_track(self, service: str, url: str) -> StreamingTrack | None:
        try:
            return await asyncio.wait_for(
                self._resolve_indirect_track_inner(service, url),
                timeout=2.5,
            )
        except TimeoutError:
            return None

    async def _resolve_indirect_track_inner(self, service: str, url: str) -> StreamingTrack | None:
        metadata = await self._extract_indirect_metadata(service, url)
        if not metadata:
            return None

        track_name = str(metadata.get("track_name") or "").strip()
        artist_name = str(metadata.get("artist") or "").strip()
        if not track_name or not artist_name:
            return None

        spotify_match = await self._spotify_search_track(track_name, artist_name)
        if not spotify_match:
            return None

        if not self._is_confident_match(
            expected_track=track_name,
            expected_artist=artist_name,
            candidate_track=spotify_match["track_name"],
            candidate_artist=spotify_match["artist"],
        ):
            return None

        return {
            "service": service,
            "track_name": spotify_match["track_name"],
            "artist": spotify_match["artist"],
            "artwork_url": spotify_match["artwork_url"],
        }

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

    async def _extract_indirect_metadata(self, service: str, url: str) -> StreamingTrack | None:
        if service == "Apple Music":
            extracted = self._extract_apple_url_metadata(url)
            if extracted and extracted.get("track_name") and extracted.get("artist"):
                return extracted
        if service == "YouTube Music":
            extracted = self._extract_youtube_url_metadata(url)
            if extracted and extracted.get("track_name") and extracted.get("artist"):
                return extracted

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TigraoRadio/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            return None

        html_body = response.text
        if service == "Apple Music":
            return self._extract_apple_html_metadata(html_body)
        if service == "YouTube Music":
            return self._extract_youtube_html_metadata(html_body)
        return None

    @staticmethod
    def _extract_apple_url_metadata(url: str) -> StreamingTrack | None:
        # URLs de Apple Music não carregam o artista com consistência no path.
        # Mantemos extração por URL desativada para evitar baixa confiança.
        return None

    @staticmethod
    def _extract_youtube_url_metadata(url: str) -> StreamingTrack | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        title_values = query.get("title")
        artist_values = query.get("artist")
        if not title_values or not artist_values:
            return None

        track_name = title_values[0].strip()
        artist_name = artist_values[0].strip()
        if not track_name or not artist_name:
            return None

        return {
            "service": "YouTube Music",
            "track_name": track_name,
            "artist": artist_name,
            "artwork_url": None,
        }

    def _extract_apple_html_metadata(self, html_body: str) -> StreamingTrack | None:
        title = self._meta_content(html_body, "og:title") or self._meta_content(
            html_body, "twitter:title"
        )
        if not title or " by " not in title:
            return None

        track_name, artist = title.split(" by ", 1)
        track_name = track_name.strip()
        artist = artist.strip()
        if not track_name or not artist:
            return None

        return {
            "service": "Apple Music",
            "track_name": track_name,
            "artist": artist,
            "artwork_url": None,
        }

    def _extract_youtube_html_metadata(self, html_body: str) -> StreamingTrack | None:
        title = self._meta_content(html_body, "og:title") or self._meta_content(
            html_body, "title"
        )
        description = self._meta_content(html_body, "og:description")

        if not title or not description:
            return None

        artist = description.split("·", 1)[0].strip()
        if not artist:
            return None

        return {
            "service": "YouTube Music",
            "track_name": title.strip(),
            "artist": artist,
            "artwork_url": None,
        }

    @staticmethod
    def _meta_content(html_body: str, key: str) -> str | None:
        for match in META_TAG_RE.finditer(html_body):
            found_key = (match.group("key") or "").strip().lower()
            if found_key == key.lower():
                raw_value = match.group("value") or ""
                value = html.unescape(raw_value).strip()
                if value:
                    return value
        return None

    async def _spotify_search_track(self, track_name: str, artist_name: str) -> StreamingTrack | None:
        token = await self._spotify_access_token()
        if not token:
            return None

        query = f"track:{track_name} artist:{artist_name}"
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                "https://api.spotify.com/v1/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "type": "track", "limit": 3},
            )

        if response.status_code != 200:
            return None

        payload = response.json()
        items = ((payload.get("tracks") or {}).get("items") or [])
        if not items:
            return None

        for item in items:
            artists = item.get("artists") or []
            album = item.get("album") or {}
            images = album.get("images") or []
            found_track = str(item.get("name") or "").strip()
            found_artist = str((artists[0] or {}).get("name") if artists else "").strip()
            if not found_track or not found_artist:
                continue

            if not self._is_confident_match(
                expected_track=track_name,
                expected_artist=artist_name,
                candidate_track=found_track,
                candidate_artist=found_artist,
            ):
                continue

            return {
                "service": "Spotify",
                "track_name": found_track,
                "artist": found_artist,
                "artwork_url": str(images[0].get("url")) if images else None,
            }

        return None

    def _is_confident_match(
        self,
        *,
        expected_track: str,
        expected_artist: str,
        candidate_track: str,
        candidate_artist: str,
    ) -> bool:
        norm_expected_track = self._normalize_text(expected_track)
        norm_expected_artist = self._normalize_text(expected_artist)
        norm_candidate_track = self._normalize_text(candidate_track)
        norm_candidate_artist = self._normalize_text(candidate_artist)

        if not all(
            [
                norm_expected_track,
                norm_expected_artist,
                norm_candidate_track,
                norm_candidate_artist,
            ]
        ):
            return False

        track_score = SequenceMatcher(None, norm_expected_track, norm_candidate_track).ratio()
        artist_score = SequenceMatcher(None, norm_expected_artist, norm_candidate_artist).ratio()
        return track_score >= 0.75 and artist_score >= 0.85

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.casefold())
        ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", " ", ascii_like).strip()


streaming_service = StreamingService()
