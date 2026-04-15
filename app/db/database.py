from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config.settings import DATA_DIR, DATABASE_URL


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db() -> None:
    """Create all database tables if they do not exist."""
    # Import models here so SQLAlchemy is aware of metadata before create_all.
    from app.models.spotify_token import SpotifyToken  # noqa: F401

    Base.metadata.create_all(bind=engine)
