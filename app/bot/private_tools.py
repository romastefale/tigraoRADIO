from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import ChatJoinRequest, Message
from sqlalchemy import text

from app.db.database import engine


OWNER_ID = 8505890439
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
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Erro: comando incompleto.\n"
            "Motivo: chat_id não informado.\n"
            "Use: /myjoin <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await message.answer(
            "Erro: chat_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
            "Use: /myjoin <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=False,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌ Failed to create direct invite link.")


@router.message(Command("mylink"))
async def mylink(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Erro: comando incompleto.\n"
            "Motivo: chat_id não informado.\n"
            "Use: /mylink <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await message.answer(
            "Erro: chat_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
            "Use: /mylink <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        invite = await message.bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
        )
        await message.answer(invite.invite_link)
    except Exception:
        await message.answer("❌ Failed to create join-request link.")


@router.message(Command("mybad"))
async def mybad(message: Message) -> None:
    _ensure_join_requests_table()

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Erro: comando incompleto.\n"
            "Motivo: chat_id ou user_id não informado.\n"
            "Use: /mybad <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
        user_id = int(parts[2])
    except Exception:
        await message.answer(
            "Erro: parâmetros inválidos.\n"
            "Motivo: chat_id ou user_id não são números válidos.\n"
            "Use: /mybad <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

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
                WHERE user_id = :user_id AND chat_id = :chat_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"user_id": user_id, "chat_id": chat_id},
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
            chat_id=chat_id,
            user_id=user_id,
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
                "user_id": user_id,
                "chat_id": chat_id,
            },
        )

    await message.answer("✅ Join request approved.")


@router.message(Command("purge"))
async def purge(message: Message) -> None:
    _ensure_join_requests_table()

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Erro: comando incompleto.\n"
            "Motivo: chat_id ou user_id não informado.\n"
            "Use: /purge <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
        user_id = int(parts[2])
    except Exception:
        await message.answer(
            "Erro: parâmetros inválidos.\n"
            "Motivo: chat_id ou user_id não são números válidos.\n"
            "Use: /purge <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        await message.bot.ban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )
    except Exception:
        await message.answer("❌")
        return

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM join_requests WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

    await message.answer("🚫")
