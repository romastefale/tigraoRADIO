from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATABASE_URL


# garante persistência no Railway
os.makedirs("/data", exist_ok=True)


# configura engine corretamente para SQLite vs outros DBs
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(DATABASE_URL)


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def init_db() -> None:
    """Create all database tables if they do not exist."""
    from app.models.spotify_token import SpotifyToken  # noqa: F401

    Base.metadata.create_all(bind=engine)
