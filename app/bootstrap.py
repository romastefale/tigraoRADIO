from __future__ import annotations

import asyncio

import uvicorn

from app.bot.telegram_bot import run_bot
from app.main import app


async def run_api() -> None:
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, reload=False)
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    await asyncio.gather(run_api(), run_bot())


if __name__ == "__main__":
    asyncio.run(main())
