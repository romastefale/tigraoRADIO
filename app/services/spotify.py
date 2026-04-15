from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import requests
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPES,
)
from app.db.models import SpotifyToken

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"


def _ensure_spotify_configured() -> None:
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise HTTPException(
            status_code=500,
            detail="Spotify credentials are not configured",
        )


def build_auth_url() -> str:
    _ensure_spotify_configured()
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SPOTIFY_SCOPES,
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"


def _save_tokens(db: Session, token_payload: dict, existing: SpotifyToken | None) -> SpotifyToken:
    expires_in = int(token_payload.get("expires_in", 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    if existing is None:
        existing = SpotifyToken(
            id=1,
            access_token=token_payload["access_token"],
            refresh_token=token_payload["refresh_token"],
            expires_at=expires_at,
        )
        db.add(existing)
    else:
        existing.access_token = token_payload["access_token"]
        existing.refresh_token = token_payload.get("refresh_token", existing.refresh_token)
        existing.expires_at = expires_at

    db.commit()
    db.refresh(existing)
    return existing


def exchange_code_for_tokens(db: Session, code: str) -> SpotifyToken:
    _ensure_spotify_configured()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange OAuth code")

    token_data = response.json()
    existing = db.get(SpotifyToken, 1)
    return _save_tokens(db, token_data, existing)


def refresh_token_if_needed(db: Session) -> SpotifyToken:
    _ensure_spotify_configured()
    token_row = db.get(SpotifyToken, 1)
    if token_row is None:
        raise HTTPException(status_code=401, detail="Spotify is not connected yet")

    now = datetime.now(UTC)
    expires_at = token_row.expires_at.replace(tzinfo=UTC)
    if expires_at > now + timedelta(seconds=30):
        return token_row

    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token_row.refresh_token,
            "client_id": SPOTIFY_CLIENT_ID,
            "client_secret": SPOTIFY_CLIENT_SECRET,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to refresh Spotify token")

    token_data = response.json()
    return _save_tokens(db, token_data, token_row)


def _spotify_get(access_token: str, path: str, params: dict | None = None) -> dict:
    response = requests.get(
        f"{SPOTIFY_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=15,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Spotify API request failed")
    return response.json()


def _format_track(item: dict | None) -> dict:
    if not item:
        raise HTTPException(status_code=404, detail="Track not found")

    album = item.get("album", {})
    images = album.get("images", [])
    return {
        "track_name": item.get("name"),
        "artist": ", ".join(artist.get("name", "") for artist in item.get("artists", [])),
        "album": album.get("name"),
        "album_cover_url": images[0].get("url") if images else None,
    }


def get_current_track(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(token_row.access_token, "/me/player/currently-playing")
    return _format_track(payload.get("item"))


def get_last_played_track(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(token_row.access_token, "/me/player/recently-played", {"limit": 1})
    items = payload.get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="No recently played tracks")
    return _format_track(items[0].get("track"))
