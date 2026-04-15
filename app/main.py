from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db, init_db
from app.services.spotify import (
    build_auth_url,
    exchange_code_for_tokens,
    get_current_track,
    get_last_played_track,
)


app = FastAPI(title="Minimal Backend")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/healthz", status_code=200)
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/spotify/login")
def spotify_login() -> RedirectResponse:
    return RedirectResponse(url=build_auth_url(), status_code=302)


@app.get("/callback")
def spotify_callback(code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    exchange_code_for_tokens(db, code)
    return {"status": "connected"}


@app.get("/spotify/current-track")
def spotify_current_track(db: Session = Depends(get_db)) -> dict:
    return get_current_track(db)


@app.get("/spotify/last-played")
def spotify_last_played(db: Session = Depends(get_db)) -> dict:
    return get_last_played_track(db)
