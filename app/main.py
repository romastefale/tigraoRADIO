from __future__ import annotations

from fastapi import FastAPI

from app.db.database import init_db


app = FastAPI(title="Minimal Backend")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/healthz", status_code=200)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
