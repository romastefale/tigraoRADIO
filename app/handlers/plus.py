from __future__ import annotations

from aiogram import Bot, Router, types
from aiogram.filters import Command

router = Router()


def parse_payload(text: str | None) -> tuple[int | None, str | None]:
    if not text:
        return None, None

    try:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        chat_id = int(lines[1])
        user_raw = lines[2]
        return chat_id, user_raw
    except Exception:
        return None, None


async def resolve_user(bot: Bot, user_raw: str) -> int | None:
    if user_raw.isdigit():
        return int(user_raw)

    if user_raw.startswith("@"):
        try:
            username = user_raw[1:]
            chat = await bot.get_chat(f"@{username}")
            return chat.id
        except Exception:
            return None

    return None


@router.message(Command("plus"))
async def plus_handler(message: types.Message) -> None:
    if message.chat.type != "private":
        return

    chat_id, user_raw = parse_payload(message.text)
    if not chat_id or not user_raw:
        return

    try:
        user_id = await resolve_user(message.bot, user_raw)
        if not user_id:
            return

        try:
            await message.bot.invite_chat_member(chat_id, user_id)
            return
        except Exception:
            link = await message.bot.create_chat_invite_link(chat_id)
            await message.answer(link.invite_link)
    except Exception:
        pass
