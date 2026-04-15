from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import requests
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config.settings import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_SCOPES
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


def build_auth_url(redirect_uri: str) -> str:
    _ensure_spotify_configured()
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
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


def exchange_code_for_tokens(db: Session, code: str, redirect_uri: str) -> SpotifyToken:
    _ensure_spotify_configured()
    response = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
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


def _spotify_get(
    access_token: str,
    path: str,
    params: dict | None = None,
    allow_empty: bool = False,
) -> dict | None:
    response = requests.get(
        f"{SPOTIFY_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=15,
    )
    if allow_empty and response.status_code == 204:
        return None
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


def _format_album(album: dict | None) -> dict:
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    images = album.get("images", [])
    artists = ", ".join(artist.get("name", "") for artist in album.get("artists", []))
    return {
        "album": album.get("name"),
        "artist": artists,
        "album_cover_url": images[0].get("url") if images else None,
    }


def _format_artist(artist: dict | None) -> dict:
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found")
    images = artist.get("images", [])
    return {
        "artist": artist.get("name"),
        "genres": artist.get("genres", []),
        "followers": artist.get("followers", {}).get("total"),
        "image_url": images[0].get("url") if images else None,
    }


def get_current_track(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(token_row.access_token, "/me/player/currently-playing", allow_empty=True)
    if payload is None:
        raise HTTPException(status_code=404, detail="No current track")
    return _format_track(payload.get("item"))


def get_last_played_track(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(token_row.access_token, "/me/player/recently-played", {"limit": 1})
    items = payload.get("items", []) if payload else []
    if not items:
        raise HTTPException(status_code=404, detail="No recently played tracks")
    return _format_track(items[0].get("track"))


def get_current_or_last_track(db: Session) -> dict:
    try:
        return get_current_track(db)
    except HTTPException:
        return get_last_played_track(db)


def get_album_info(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(token_row.access_token, "/me/player/currently-playing", allow_empty=True)
    if payload and payload.get("item"):
        return _format_album(payload["item"].get("album"))

    recent = _spotify_get(token_row.access_token, "/me/player/recently-played", {"limit": 1})
    items = recent.get("items", []) if recent else []
    if not items:
        raise HTTPException(status_code=404, detail="Album not found")
    return _format_album(items[0].get("track", {}).get("album"))


def get_artist_info(db: Session) -> dict:
    token_row = refresh_token_if_needed(db)
    track = get_current_or_last_track(db)
    search_q = f"track:{track['track_name']} artist:{track['artist'].split(',')[0]}"
    payload = _spotify_get(
        token_row.access_token,
        "/search",
        {"q": search_q, "type": "track", "limit": 1},
    )
    items = payload.get("tracks", {}).get("items", []) if payload else []
    if not items:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist = (items[0].get("artists") or [{}])[0]
    artist_id = artist.get("id")
    if not artist_id:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist_payload = _spotify_get(token_row.access_token, f"/artists/{artist_id}")
    return _format_artist(artist_payload)


def get_top_tracks(db: Session, limit: int = 5) -> list[dict]:
    token_row = refresh_token_if_needed(db)
    payload = _spotify_get(
        token_row.access_token,
        "/me/top/tracks",
        {"time_range": "short_term", "limit": limit},
    )
    items = payload.get("items", []) if payload else []
    return [_format_track(item) for item in items]
