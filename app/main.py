from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import SessionLocal, init_db
from app.services.spotify import (
    build_auth_url,
    exchange_code_for_token,
    get_current_or_last_played,
)


app = FastAPI(title="Minimal Backend")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/healthz", status_code=200)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/spotify/login")
def spotify_login() -> RedirectResponse:
    return RedirectResponse(url=build_auth_url())


@app.get("/callback")
def spotify_callback(code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    exchange_code_for_token(db, code)
    return {"status": "ok", "message": "Spotify OAuth completed and tokens saved."}


@app.get("/spotify/track")
def spotify_track(db: Session = Depends(get_db)) -> dict[str, str | None]:
    return get_current_or_last_played(db)
