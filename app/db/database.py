from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATABASE_URL


logger = logging.getLogger(__name__)

# ========================
# PREPARAR DIRETÓRIO /data (RAILWAY)
# ========================

try:
    os.makedirs("/data", exist_ok=True)
    logger.info("Database directory /data is available.")
except Exception as exc:  # noqa: BLE001
    logger.warning("Could not prepare /data, fallback may be used: %s", exc)


# ========================
# ENGINE CONFIG
# ========================

connect_args: dict = {}

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


# ========================
# INIT DATABASE
# ========================

def init_db() -> None:
    """Create all database tables if they do not exist."""
    from app.models.spotify_token import SpotifyToken  # noqa: F401

    logger.info("Initializing database with URL: %s", DATABASE_URL)
    Base.metadata.create_all(bind=engine)