from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.types import ChatJoinRequest, Message
from sqlalchemy import text

from app.db.database import engine


OWNER_ID = 8505890439
MAIN_GROUP_ID = -1002556760909
APPROVAL_WINDOW = timedelta(hours=2)
SINGLE_USE_EXPIRY = timedelta(minutes=5)

router = Router(name="private_tools")
router.message.filter(F.from_user.id == OWNER_ID, F.chat.type == "private")


def _ensure_join_requests_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS join_requests (
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )


def _parse_target_user_id(raw_text: str) -> int | None:
    if not raw_text.startswith("!mybad ") or not raw_text.endswith("¡"):
        return None

    payload = raw_text[len("!mybad ") : -1].strip()
    if not payload or not payload.isdigit():
        return None

    try:
        return int(payload)
    except ValueError:
        return None


def _to_utc(value: object) -> datetime | None:
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
async def store_join_request(join_request: ChatJoinRequest) -> None:
    _ensure_join_requests_table()
    created_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO join_requests (user_id, chat_id, created_at)
                VALUES (:user_id, :chat_id, :created_at)
                """
            ),
            {
                "user_id": join_request.from_user.id,
                "chat_id": join_request.chat.id,
                "created_at": created_at,
            },
        )


@router.message(F.text.startswith("!mybad"))
async def approve_join_request(message: Message) -> None:
    raw_text = message.text or ""
    target_user_id = _parse_target_user_id(raw_text)
    if target_user_id is None:
        return

    _ensure_join_requests_table()
    with engine.begin() as conn:
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
        await message.answer("❌")
        return

    created_at = _to_utc(row["created_at"])
    if created_at is None:
        await message.answer("❌")
        return

    if datetime.now(timezone.utc) - created_at > APPROVAL_WINDOW:
        await message.answer("❌")
        return

    try:
        await message.bot.approve_chat_join_request(
            chat_id=int(row["chat_id"]),
            user_id=int(row["user_id"]),
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    DELETE FROM join_requests
                    WHERE user_id = :user_id AND chat_id = :chat_id
                    """
                ),
                {"user_id": int(row["user_id"]), "chat_id": int(row["chat_id"])},
            )
        await message.answer("✅")
    except Exception:
        await message.answer("❌")


@router.message(F.text == "!mylink¡")
async def create_join_request_link(message: Message) -> None:
    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=MAIN_GROUP_ID,
            creates_join_request=True,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌")


@router.message(F.text == "!myjoin¡")
async def create_single_use_link(message: Message) -> None:
    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=MAIN_GROUP_ID,
            creates_join_request=False,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌")
