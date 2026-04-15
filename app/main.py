from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator

from fastapi import Depends, FastAPI, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.bot.telegram import shutdown_telegram_bot, startup_telegram_bot
from app.db.database import SessionLocal, init_db
from app.services.spotify import spotify_service


app = FastAPI(title="Minimal Backend")
logger = logging.getLogger(__name__)


def _log_background_task_result(task: asyncio.Task[None], task_name: str) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:  # noqa: BLE001
        logger.exception("Background task '%s' failed", task_name)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()

    telegram_startup_task = asyncio.create_task(startup_telegram_bot())
    telegram_startup_task.add_done_callback(
        lambda task: _log_background_task_result(task, "telegram_startup")
    )


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
    state: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    resolved_user_id = spotify_service.resolve_user_id_from_state(state)
    if resolved_user_id is None:
        return {"status": "error", "message": "Invalid OAuth state."}

    await spotify_service.exchange_code_for_token(db, code, resolved_user_id)
    return {"status": "ok", "message": "Spotify OAuth completed and tokens saved."}


@app.get("/spotify/track")
async def spotify_track(
    user_id: int, db: Session = Depends(get_db)
) -> dict[str, str | None] | None:
    return await spotify_service.get_current_or_last_played(db, user_id)