from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.services.spotify import spotify_service

router = Router()

OWNER_ID = 8505890439

station_active = False
station_task: asyncio.Task | None = None
station_message_id: int | None = None
station_chat_id: int | None = None
station_started_at: datetime | None = None
last_track_id: str | None = None


@router.message(Command("station"))
async def station_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    await toggle_station(message)


async def toggle_station(message: Message):
    global station_active

    if not station_active:
        await start_station(message)
    else:
        await stop_station()


async def start_station(message: Message):
    global station_active, station_task
    global station_message_id, station_chat_id, station_started_at

    sent = await message.answer("🎧 Iniciando station...")

    station_active = True
    station_message_id = sent.message_id
    station_chat_id = sent.chat.id
    station_started_at = datetime.now()

    station_task = asyncio.create_task(station_loop())


async def stop_station():
    global station_active, station_task

    station_active = False

    if station_task:
        station_task.cancel()
        station_task = None

    try:
        from app.bot.telegram import bot  # ajuste se seu import for diferente

        await bot.edit_message_text(
            "🎧 Station encerrada",
            chat_id=station_chat_id,
            message_id=station_message_id
        )
    except:
        pass


async def station_loop():
    global last_track_id

    from app.bot.telegram import bot  # ajuste se necessário

    try:
        while True:
            # timeout 2h
            if datetime.now() - station_started_at > timedelta(hours=2):
                await stop_station()
                return

            track = await spotify_service.get_current_track()

            if track:
                track_id = track.get("id")

                if track_id != last_track_id:
                    last_track_id = track_id

                    text = f"🎧 Você está ouvindo\n{track['name']} — {track['artist']}"

                    try:
                        await bot.edit_message_text(
                            text,
                            chat_id=station_chat_id,
                            message_id=station_message_id
                        )
                    except:
                        pass

            await asyncio.sleep(5)

    except asyncio.CancelledError:
        return