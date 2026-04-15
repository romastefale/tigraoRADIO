from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES,
)
from app.models.spotify_token import SpotifyToken


AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
NOW_PLAYING_URL = "https://api.spotify.com/v1/me/player/currently-playing"
RECENTLY_PLAYED_URL = "https://api.spotify.com/v1/me/player/recently-played?limit=1"


def _basic_auth_header() -> str:
    creds = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")
    return "Basic " + base64.b64encode(creds).decode("utf-8")


def _spotify_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    encoded_data = None
    req_headers = headers or {}

    if data is not None:
        encoded_data = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(url, data=encoded_data, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")
            return status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else ""
        parsed = json.loads(body) if body else None
        return exc.code, parsed


def build_auth_url() -> str:
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(status_code=500, detail="SPOTIFY_CLIENT_ID is not configured")

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": SPOTIFY_CLIENT_ID,
            "scope": SPOTIFY_SCOPES,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
        }
    )
    return f"{AUTH_URL}?{query}"


def save_token(
    db: Session,
    access_token: str,
    refresh_token: str,
    expires_in: int,
) -> SpotifyToken:
    expiration = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token_row = db.get(SpotifyToken, 1)

    if token_row is None:
        token_row = SpotifyToken(
            id=1,
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


def exchange_code_for_token(db: Session, code: str) -> SpotifyToken:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Spotify client credentials are not configured")

    status, payload = _spotify_request(
        TOKEN_URL,
        method="POST",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
        },
    )

    if status != 200 or payload is None:
        raise HTTPException(status_code=400, detail=f"Spotify token exchange failed: {payload}")

    return save_token(
        db=db,
        access_token=payload["access_token"],
        refresh_token=payload["refresh_token"],
        expires_in=payload["expires_in"],
    )


def refresh_token_if_needed(db: Session) -> SpotifyToken:
    token_row = db.get(SpotifyToken, 1)
    if token_row is None:
        raise HTTPException(status_code=404, detail="No Spotify token found. Complete OAuth login first.")

    now_utc = datetime.now(timezone.utc)
    expiration_utc = token_row.expiration.replace(tzinfo=timezone.utc)

    if expiration_utc > now_utc + timedelta(seconds=30):
        return token_row

    status, payload = _spotify_request(
        TOKEN_URL,
        method="POST",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_row.refresh_token,
        },
    )

    if status != 200 or payload is None:
        raise HTTPException(status_code=400, detail=f"Spotify token refresh failed: {payload}")

    new_refresh_token = payload.get("refresh_token", token_row.refresh_token)
    return save_token(
        db=db,
        access_token=payload["access_token"],
        refresh_token=new_refresh_token,
        expires_in=payload["expires_in"],
    )


def _map_track(item: dict[str, Any] | None) -> dict[str, Any] | None:
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


def get_current_track(db: Session) -> dict[str, Any] | None:
    token_row = refresh_token_if_needed(db)

    status, payload = _spotify_request(
        NOW_PLAYING_URL,
        headers={"Authorization": f"Bearer {token_row.access_token}"},
    )

    if status == 200 and payload:
        return _map_track(payload.get("item"))

    if status in (204, 404):
        return None

    if status == 401:
        token_row = refresh_token_if_needed(db)
        status, payload = _spotify_request(
            NOW_PLAYING_URL,
            headers={"Authorization": f"Bearer {token_row.access_token}"},
        )
        if status == 200 and payload:
            return _map_track(payload.get("item"))

    raise HTTPException(status_code=400, detail=f"Spotify current track lookup failed: {payload}")


def get_last_played_track(db: Session) -> dict[str, Any] | None:
    token_row = refresh_token_if_needed(db)

    status, payload = _spotify_request(
        RECENTLY_PLAYED_URL,
        headers={"Authorization": f"Bearer {token_row.access_token}"},
    )

    if status == 200 and payload:
        items = payload.get("items", [])
        if not items:
            return None
        return _map_track(items[0].get("track"))

    raise HTTPException(status_code=400, detail=f"Spotify recently played lookup failed: {payload}")


def get_current_or_last_played(db: Session) -> dict[str, Any]:
    current = get_current_track(db)
    if current:
        return {"source": "currently_playing", **current}

    last_played = get_last_played_track(db)
    if last_played:
        return {"source": "recently_played", **last_played}

    raise HTTPException(status_code=404, detail="No current or recently played track found")
