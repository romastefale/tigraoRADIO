from __future__ import annotations

import asyncio

from aiogram import Router, types
from aiogram.filters import Command

router = Router()


KICK_RESET_DELAY_SECONDS = 1


def parse_payload(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None

    try:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        chat_id = int(lines[1])
        user_id = int(lines[2])
        return chat_id, user_id
    except Exception:
        return None, None


async def kick_reset_user(bot, chat_id: int, user_id: int) -> None:
    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    await asyncio.sleep(KICK_RESET_DELAY_SECONDS)
    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)


@router.message(Command("vvv"))
async def vvv(message: types.Message) -> None:
    if message.chat.type != "private":
        return

    chat_id, user_id = parse_payload(message.text)
    if not chat_id or not user_id:
        return

    try:
        await kick_reset_user(message.bot, chat_id, user_id)
    except Exception:
        pass
