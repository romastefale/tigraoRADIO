from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Generator

from fastapi import Depends, FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
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
    except Exception:
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

    logger.error("CALLBACK RECEIVED")
    logger.error("CODE RECEIVED")
    logger.error("STATE RECEIVED: %s", state)

    user_id = spotify_service.resolve_user_id_from_state(state)
    logger.error("RESOLVED USER_ID: %s", user_id)

    if user_id is None:
        logger.error("INVALID STATE")
        return {
            "status": "error",
            "message": "Invalid state. Use /login novamente.",
        }

    try:
        await spotify_service.exchange_code_for_token(db, code, user_id)
        logger.error("TOKEN FLOW COMPLETED")
    except Exception as e:
        logger.error("TOKEN FLOW FAILED: %s", e)
        return {
            "status": "error",
            "message": "Erro ao conectar com Spotify",
        }

    return {
        "status": "ok",
        "message": "Spotify conectado com sucesso!",
    }


@app.get("/webapp")
def webapp_player(
    track_name: str | None = Query(default=None),
    artist_name: str | None = Query(default=None),
    album_name: str | None = Query(default=None),
    cover_url: str | None = Query(default=None),
) -> HTMLResponse:
    with open("app/webapp/player.html", encoding="utf-8") as html_file:
        html_content = html_file.read()

    player_data = {
        "track_name": track_name,
        "artist_name": artist_name,
        "album_name": album_name,
        "cover_url": cover_url,
    }
    safe_player_data = json.dumps(player_data, ensure_ascii=False).replace("<", "\\u003c")

    return HTMLResponse(html_content.replace("__PLAYER_DATA__", safe_player_data))


@app.get("/spotify/track")
async def spotify_track(
    user_id: int,
    db: Session = Depends(get_db),
) -> dict[str, str | None] | None:
    return await spotify_service.get_current_or_last_played(db, user_id)
