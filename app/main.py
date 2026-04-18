from __future__ import annotations

import asyncio
import logging
from collections.abc import Generator

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from aiogram import Bot

from app.bot.telegram import shutdown_telegram_bot, startup_telegram_bot, bot_dispatcher
from app.config.settings import TELEGRAM_BOT_TOKEN
from app.db.database import SessionLocal, init_db
from app.likes import likes_service
from app.services.spotify import spotify_service


app = FastAPI(title="Minimal Backend")
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_BOT_TOKEN)


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
    likes_service.initialize()

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


@app.get("/spotify/track")
async def spotify_track(
    user_id: int,
    db: Session = Depends(get_db),
) -> dict[str, str | None] | None:
    return await spotify_service.get_current_or_last_played(db, user_id)


# 🔥 CORREÇÃO: ROTA DO WEBHOOK (ESSENCIAL)
@app.post("/webhook")
async def telegram_webhook(request: Request):
    update = await request.json()
    await bot_dispatcher.feed_update(bot, update)
    return {"ok": True}
