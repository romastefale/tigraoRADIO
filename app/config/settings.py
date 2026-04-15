from __future__ import annotations

import os
from pathlib import Path

# base do projeto
BASE_DIR = Path(__file__).resolve().parents[2]

# ========================
# TELEGRAM
# ========================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ========================
# SPOTIFY
# ========================

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
SPOTIFY_REDIRECT_URI = f"{BASE_URL.rstrip('/')}/callback"

SPOTIFY_SCOPES = "user-read-currently-playing user-read-recently-played"

# ========================
# SPOTIFY PERFORMANCE / RESILIENCE
# ========================

SPOTIFY_HTTP_TIMEOUT_SECONDS = float(os.getenv("SPOTIFY_HTTP_TIMEOUT_SECONDS", "10"))
SPOTIFY_MAX_CONCURRENT_REQUESTS = int(os.getenv("SPOTIFY_MAX_CONCURRENT_REQUESTS", "10"))

SPOTIFY_CACHE_TTL_SECONDS = float(os.getenv("SPOTIFY_CACHE_TTL_SECONDS", "5"))
SPOTIFY_CACHE_MAX_ENTRIES = int(os.getenv("SPOTIFY_CACHE_MAX_ENTRIES", "500"))

SPOTIFY_PER_USER_RATE_LIMIT = int(os.getenv("SPOTIFY_PER_USER_RATE_LIMIT", "10"))
SPOTIFY_RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("SPOTIFY_RATE_LIMIT_WINDOW_SECONDS", "5"))

SPOTIFY_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("SPOTIFY_CIRCUIT_BREAKER_THRESHOLD", "3"))
SPOTIFY_CIRCUIT_BREAKER_COOLDOWN_SECONDS = float(
    os.getenv("SPOTIFY_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "8")
)

# ========================
# DATABASE (AUTO SAFE)
# ========================

# prioridade:
# 1. DATABASE_URL (se existir)
# 2. Railway volume (/data)
# 3. fallback local (./app.db)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    if Path("/data").exists():
        DATABASE_URL = "sqlite:////data/app.db"
    else:
        DATABASE_URL = f"sqlite:///{(BASE_DIR / 'app.db').resolve()}"