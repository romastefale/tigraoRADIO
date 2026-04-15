from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'app.db').resolve()}"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv(
    "SPOTIFY_REDIRECT_URI", "http://localhost:8000/callback"
)
SPOTIFY_SCOPES = os.getenv(
    "SPOTIFY_SCOPES",
    "user-read-currently-playing user-read-recently-played user-top-read",
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_RATE_LIMIT_SECONDS = int(os.getenv("TELEGRAM_RATE_LIMIT_SECONDS", "2"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "5"))
