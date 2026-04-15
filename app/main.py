from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, FastAPI, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.bot.telegram import shutdown_telegram_bot, startup_telegram_bot
from app.db.database import SessionLocal, init_db
from app.services.spotify import spotify_service


app = FastAPI(title="Minimal Backend")


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    await spotify_service.startup()
    await startup_telegram_bot()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await shutdown_telegram_bot()
    await spotify_service.shutdown()


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
def spotify_login(user_id: int = Query(...)) -> RedirectResponse:
    return RedirectResponse(url=spotify_service.build_auth_url(user_id))


@app.get("/callback")
async def spotify_callback(
    code: str,
    db: Session = Depends(get_db),
    state: str | None = None,
    user_id: int | None = None,
) -> dict[str, str]:
    resolved_user_id = user_id
    if resolved_user_id is None and state is not None and state.isdigit():
        resolved_user_id = int(state)

    if resolved_user_id is None:
        return {"status": "error", "message": "user_id was not provided"}

    await spotify_service.exchange_code_for_token(db, code, resolved_user_id)
    return {"status": "ok", "message": "Spotify OAuth completed and tokens saved."}


@app.get("/spotify/track")
async def spotify_track(user_id: int, db: Session = Depends(get_db)) -> dict[str, str | None]:
    return await spotify_service.get_current_or_last_played(db, user_id)


@app.get("/spotify/album")
async def spotify_album(user_id: int, db: Session = Depends(get_db)) -> dict[str, str | None]:
    return await spotify_service.get_album_info(db, user_id)


@app.get("/spotify/artist")
async def spotify_artist(user_id: int, db: Session = Depends(get_db)) -> dict[str, str | None]:
    return await spotify_service.get_artist_info(db, user_id)


@app.get("/spotify/ranking")
async def spotify_ranking(user_id: int, db: Session = Depends(get_db)) -> dict[str, list[dict[str, str | None]]]:
    return await spotify_service.get_top_tracks(db, user_id)
