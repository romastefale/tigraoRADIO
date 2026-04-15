from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATABASE_URL


logger = logging.getLogger(__name__)

# ========================
# PREPARAR /data COM SEGURANÇA
# ========================

try:
    os.makedirs("/data", exist_ok=True)
    logger.info("Database directory /data ready.")
except Exception as exc:  # noqa: BLE001
    logger.warning("Could not prepare /data: %s", exc)


# ========================
# ENGINE SEGURO
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
# INIT
# ========================

def init_db() -> None:
    """Create tables safely without crashing the app."""
    try:
        from app.models.spotify_token import SpotifyToken  # noqa: F401

        Base.metadata.create_all(bind=engine)
        logger.info("Database initialized.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Database initialization failed: %s", exc)