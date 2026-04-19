from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import ChatJoinRequest, Message
from sqlalchemy import text

from app.db.database import engine


OWNER_ID = 8505890439
MAIN_GROUP_ID = -1002556760909
APPROVAL_WINDOW = timedelta(hours=2)
SINGLE_USE_EXPIRY = timedelta(minutes=5)

router = Router(name="private_tools")
router.message.filter(
    F.from_user.id == OWNER_ID,
    F.chat.type == "private",
)


def _ensure_join_requests_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS join_requests (
                    user_id INTEGER,
                    chat_id INTEGER,
                    created_at DATETIME
                );
                """
            )
        )


def _parse_created_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest) -> None:
    _ensure_join_requests_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO join_requests (user_id, chat_id, created_at)
                VALUES (:user_id, :chat_id, :created_at)
                """
            ),
            {
                "user_id": event.from_user.id,
                "chat_id": event.chat.id,
                "created_at": datetime.now(timezone.utc),
            },
        )


@router.message(Command("myjoin"))
async def myjoin(message: Message) -> None:
    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=MAIN_GROUP_ID,
            creates_join_request=False,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌ Failed to create direct invite link.")


@router.message(Command("mylink"))
async def mylink(message: Message) -> None:
    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=MAIN_GROUP_ID,
            creates_join_request=True,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌ Failed to create join-request link.")


@router.message(Command("mybad"))
async def mybad(message: Message) -> None:
    _ensure_join_requests_table()

    args = (message.text or "").split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.answer("Usage: /mybad <user_id>")
        return

    target_user_id = int(args[1].strip())
    cutoff = datetime.now(timezone.utc) - APPROVAL_WINDOW

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM join_requests WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )

        row = conn.execute(
            text(
                """
                SELECT user_id, chat_id, created_at
                FROM join_requests
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": target_user_id},
        ).mappings().first()

    if not row:
        await message.answer("❌ No recent join request found for this user.")
        return

    created_at = _parse_created_at(row["created_at"])
    if created_at is None or created_at < cutoff:
        await message.answer("❌ Join request expired.")
        return

    try:
        await message.bot.approve_chat_join_request(
            chat_id=int(row["chat_id"]),
            user_id=int(row["user_id"]),
        )
    except Exception:
        await message.answer("❌ Failed to approve join request.")
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM join_requests
                WHERE user_id = :user_id AND chat_id = :chat_id
                """
            ),
            {
                "user_id": int(row["user_id"]),
                "chat_id": int(row["chat_id"]),
            },
        )

    await message.answer("✅ Join request approved.")
