from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
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
            "Erro: comando inválido.\n"
            "Motivo: formato incorreto.\n"
            "Exemplo correto:\n"
            "/myjoin <chat_id>\n"
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
        await message.answer(
            f"Sucesso.\n\n"
            f"Grupo: {chat_id}\n"
            f"Link de entrada direta gerado:\n"
            f"{invite.invite_link}"
        )
    except TelegramForbiddenError:
        await message.answer(
            "Erro: operação não permitida.\n"
            "Motivo: o bot não possui permissão no grupo informado.\n"
            "Verifique se o bot é administrador.\n"
            "Tente novamente."
        )
    except Exception:
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )


@router.message(Command("mylink"))
async def mylink(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer(
            "Erro: comando inválido.\n"
            "Motivo: formato incorreto.\n"
            "Exemplo correto:\n"
            "/mylink <chat_id>\n"
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
        await message.answer(
            f"Sucesso.\n\n"
            f"Grupo: {chat_id}\n"
            f"Link de solicitação de entrada gerado:\n"
            f"{invite.invite_link}"
        )
    except TelegramForbiddenError:
        await message.answer(
            "Erro: operação não permitida.\n"
            "Motivo: o bot não possui permissão no grupo informado.\n"
            "Verifique se o bot é administrador.\n"
            "Tente novamente."
        )
    except Exception:
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )


@router.message(Command("mybad"))
async def mybad(message: Message) -> None:
    _ensure_join_requests_table()

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Erro: comando inválido.\n"
            "Motivo: formato incorreto.\n"
            "Exemplo correto:\n"
            "/mybad <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await message.answer(
            "Erro: chat_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
            "Use: /mybad <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        user_id = int(parts[2])
    except Exception:
        await message.answer(
            "Erro: user_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
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
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )
        return

    created_at = _parse_created_at(row["created_at"])
    if created_at is None or created_at < cutoff:
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )
        return

    try:
        await message.bot.approve_chat_join_request(
            chat_id=chat_id,
            user_id=user_id,
        )
    except TelegramForbiddenError:
        await message.answer(
            "Erro: operação não permitida.\n"
            "Motivo: o bot não possui permissão no grupo informado.\n"
            "Verifique se o bot é administrador.\n"
            "Tente novamente."
        )
        return
    except Exception:
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )
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

    await message.answer(
        f"Sucesso.\n\n"
        f"Grupo: {chat_id}\n"
        f"Usuário {user_id} aprovado."
    )


@router.message(Command("purge"))
async def purge(message: Message) -> None:
    _ensure_join_requests_table()

    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Erro: comando inválido.\n"
            "Motivo: formato incorreto.\n"
            "Exemplo correto:\n"
            "/purge <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await message.answer(
            "Erro: chat_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
            "Use: /purge <chat_id>\n"
            "Tente novamente."
        )
        return

    try:
        user_id = int(parts[2])
    except Exception:
        await message.answer(
            "Erro: user_id inválido.\n"
            "Motivo: o valor informado não é um número válido.\n"
            "Use: /purge <chat_id> <user_id>\n"
            "Tente novamente."
        )
        return

    try:
        await message.bot.ban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )
    except TelegramForbiddenError:
        await message.answer(
            "Erro: operação não permitida.\n"
            "Motivo: o bot não possui permissão no grupo informado.\n"
            "Verifique se o bot é administrador.\n"
            "Tente novamente."
        )
        return
    except Exception:
        await message.answer(
            "Erro: falha na execução.\n"
            "Motivo: não foi possível completar a ação.\n"
            "Verifique os dados informados.\n"
            "Tente novamente."
        )
        return

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM join_requests WHERE user_id = :user_id"),
            {"user_id": user_id},
        )

    await message.answer(
        f"Sucesso.\n\n"
        f"Grupo: {chat_id}\n"
        f"Usuário {user_id} removido."
    )
