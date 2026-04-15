from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'app.db').resolve()}"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/callback")
SPOTIFY_SCOPES = "user-read-currently-playing user-read-recently-played user-top-read"

SPOTIFY_HTTP_TIMEOUT_SECONDS = float(os.getenv("SPOTIFY_HTTP_TIMEOUT_SECONDS", "10"))
SPOTIFY_MAX_CONCURRENT_REQUESTS = int(os.getenv("SPOTIFY_MAX_CONCURRENT_REQUESTS", "10"))
SPOTIFY_CACHE_TTL_SECONDS = float(os.getenv("SPOTIFY_CACHE_TTL_SECONDS", "5"))
SPOTIFY_PER_USER_RATE_LIMIT = int(os.getenv("SPOTIFY_PER_USER_RATE_LIMIT", "10"))
SPOTIFY_RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("SPOTIFY_RATE_LIMIT_WINDOW_SECONDS", "5"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
