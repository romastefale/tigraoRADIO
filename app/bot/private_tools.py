from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    ChatJoinRequest,
    ChatMemberUpdated,
    ChatPermissions,
    Message,
    BufferedInputFile,
)
from sqlalchemy import text

from app.db.database import engine


logger = logging.getLogger(__name__)

OWNER_ID = 8505890439
APPROVAL_WINDOW = timedelta(hours=2)
SINGLE_USE_EXPIRY = timedelta(minutes=5)

router = Router(name="private_tools")


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


def _ensure_known_groups_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS known_groups (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    updated_at DATETIME
                );
                """
            )
        )


def _ensure_group_rules_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS group_rules (
                    chat_id INTEGER,
                    rule_type TEXT,
                    payload TEXT,
                    updated_at DATETIME,
                    PRIMARY KEY (chat_id, rule_type)
                );
                """
            )
        )


def _ensure_warns_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS warns (
                    chat_id INTEGER,
                    user_id INTEGER,
                    reason TEXT,
                    created_at DATETIME
                );
                """
            )
        )


def _ensure_all_tables() -> None:
    _ensure_join_requests_table()
    _ensure_known_groups_table()
    _ensure_group_rules_table()
    _ensure_warns_table()


def _is_owner_private_message(message: Message) -> bool:
    return bool(
        message.from_user
        and message.from_user.id == OWNER_ID
        and message.chat.type == "private"
    )


def _error_text(reason: str, fix: str) -> str:
    return f"Erro:\nMotivo: {reason}\nComo corrigir: {fix}"


def _success_text(title: str, details: str) -> str:
    return f"Sucesso.\n\n{title}\n{details}"




def _parse_duration(value: str):
    value = value.strip().lower()

    if value == "i":
        return "indefinido"

    if value == "x":
        return "unmute"

    if value.isdigit():
        return timedelta(minutes=int(value))

    if value.endswith("m"):
        return timedelta(minutes=int(value[:-1]))

    if value.endswith("h"):
        return timedelta(hours=int(value[:-1]))

    if value.endswith("d"):
        return timedelta(days=int(value[:-1]))

    raise ValueError("duração inválida")
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


def _remember_group(chat_id: int, title: str | None) -> None:
    _ensure_known_groups_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO known_groups (chat_id, title, updated_at)
                VALUES (:chat_id, :title, :updated_at)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "chat_id": chat_id,
                "title": title or str(chat_id),
                "updated_at": datetime.now(timezone.utc),
            },
        )


def _get_known_groups() -> list[dict[str, object]]:
    _ensure_known_groups_table()

    with engine.begin() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT chat_id, title, updated_at
                    FROM known_groups
                    ORDER BY updated_at DESC
                    """
                )
            )
            .mappings()
            .all()
        )

    return [dict(row) for row in rows]


def _save_rule(chat_id: int, rule_type: str, payload: dict[str, object]) -> None:
    _ensure_group_rules_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO group_rules (chat_id, rule_type, payload, updated_at)
                VALUES (:chat_id, :rule_type, :payload, :updated_at)
                ON CONFLICT(chat_id, rule_type) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "chat_id": chat_id,
                "rule_type": rule_type,
                "payload": json.dumps(payload, ensure_ascii=False),
                "updated_at": datetime.now(timezone.utc),
            },
        )


def _get_rule(chat_id: int, rule_type: str) -> dict[str, object] | None:
    _ensure_group_rules_table()

    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT payload
                    FROM group_rules
                    WHERE chat_id = :chat_id AND rule_type = :rule_type
                    """
                ),
                {
                    "chat_id": chat_id,
                    "rule_type": rule_type,
                },
            )
            .mappings()
            .first()
        )

    if not row or not row["payload"]:
        return None

    try:
        data = json.loads(row["payload"])
        logger.warning("DXX LOAD | chat_id=%s | data=%s", chat_id, data)
        return data
    except Exception:
        logger.exception("Falha ao decodificar payload de regra: chat_id=%s rule_type=%s", chat_id, rule_type)
        return None


def _notify_enabled(chat_id: int) -> bool:
    payload = _get_rule(chat_id, "notify")
    if not payload:
        return False

    return bool(payload.get("enabled"))


async def _notify_owner(bot, chat_id: int, text_message: str) -> None:
    if not _notify_enabled(chat_id):
        return

    try:
        await bot.send_message(
            chat_id=OWNER_ID,
            text=text_message,
        )
    except Exception:
        logger.exception("Falha ao notificar owner: chat_id=%s", chat_id)


def _add_warn(chat_id: int, user_id: int, reason: str) -> None:
    _ensure_warns_table()

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO warns (chat_id, user_id, reason, created_at)
                VALUES (:chat_id, :user_id, :reason, :created_at)
                """
            ),
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "reason": reason,
                "created_at": datetime.now(timezone.utc),
            },
        )


async def _execute_action(
    bot,
    chat_id: int,
    user_id: int,
    action: str,
    duration_minutes: int | None = None,
) -> None:
    if not user_id:
        return

    if action == "vanish":
        await bot.ban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )
        return

    if action == "unvanish":
        await bot.unban_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )
        return

    if action == "mute":
        if duration_minutes is None or duration_minutes == 0:
            until = None  # mute permanente
        else:
            until = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        return

    if action == "warn":
        _add_warn(chat_id, user_id, "manual_warn")
        return

    raise ValueError(f"ação inválida: {action}")


def _lines(message: Message) -> list[str]:
    return [line.strip() for line in (message.text or "").splitlines() if line.strip()]


def _parse_chat_id(value: str) -> int:
    return int(value.strip())


def _parse_user_id(value: str) -> int:
    return int(value.strip())


def _format_known_groups() -> str:
    groups = _get_known_groups()

    if not groups:
        return "Nenhum grupo registrado."

    output = ["GRUPOS REGISTRADOS\n"]
    for group in groups:
        output.append(f"{group['chat_id']} — {group.get('title') or group['chat_id']}")

    return "\n".join(output)


def _parse_message_link(link: str) -> tuple[int, int]:
    match = re.search(r"/c/(\d+)/(\d+)", link)
    if not match:
        raise ValueError("link inválido")

    chat_id = int("-100" + match.group(1))
    message_id = int(match.group(2))

    return chat_id, message_id


@router.chat_join_request()
async def handle_join_request(event: ChatJoinRequest) -> None:
    _ensure_join_requests_table()
    _remember_group(event.chat.id, event.chat.title or str(event.chat.id))

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


@router.my_chat_member()
async def handle_my_chat_member(event: ChatMemberUpdated) -> None:
    if event.chat.type in {"group", "supergroup"}:
        _remember_group(event.chat.id, event.chat.title or str(event.chat.id))


@router.message(Command("addgroup"))
async def addgroup(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 2:
        await message.answer(
            _error_text(
                "formato incorreto",
                "use:\n/addgroup\n<chat_id>\n<nome opcional>",
            )
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
    except Exception:
        await message.answer(
            _error_text(
                "chat_id inválido",
                "envie um número válido, exemplo: -1001234567890",
            )
        )
        return

    manual_title = lines[2] if len(lines) >= 3 else None

    try:
        chat = await message.bot.get_chat(chat_id)
        title = chat.title or manual_title or str(chat_id)
        _remember_group(chat.id, title)
        await message.answer(
            _success_text(
                "Grupo registrado.",
                f"Grupo: {title}\nID: {chat.id}",
            )
        )
    except Exception:
        if manual_title:
            _remember_group(chat_id, manual_title)
            await message.answer(
                _success_text(
                    "Grupo registrado manualmente.",
                    f"Grupo: {manual_title}\nID: {chat_id}",
                )
            )
            return

        await message.answer(
            _error_text(
                "não foi possível acessar o grupo",
                "verifique se o bot está no grupo ou use:\n/addgroup\n<chat_id>\n<nome>",
            )
        )


@router.message(Command("groups"))
async def groups(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    await message.answer(_format_known_groups())


@router.message(Command("dxx"))
async def dxx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)

    if len(lines) < 2:
        await message.answer(
            "Use:\n"
            "/dxx\n"
            "<link_da_mensagem>\n"
            "[outros links opcionais]"
        )
        return

    success = 0
    failed = 0

    for link in lines[1:]:
        try:
            chat_id, message_id = _parse_message_link(link)
            await message.bot.delete_message(chat_id, message_id)
            success += 1
        except Exception:
            failed += 1
            logger.exception("Falha ao apagar mensagem: %s", link)

    await message.answer(f"Resultado:\nApagadas: {success}\nFalhas: {failed}")


@router.message(Command("rules"))
async def rules(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 2:
        await message.answer(
            "Use:\n"
            "/rules\n"
            "<chat_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
    except Exception:
        await message.answer(
            _error_text(
                "chat_id inválido",
                "envie um número válido, exemplo: -1001234567890",
            )
        )
        return

    await message.answer("Nenhuma regra automática de /dxx ativa.")


@router.message(Command("mx1"))
async def mx1(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 2:
        await message.answer(
            "Título: Link direto\n"
            "Descrição: Gera link de entrada imediata, uso único e expiração curta.\n\n"
            "Use:\n"
            "/mx1\n"
            "<chat_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        invite = await message.bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=False,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
        )
        _remember_group(chat_id, str(chat_id))
        await message.answer(
            _success_text(
                "Link de entrada direta gerado.",
                f"Grupo: {chat_id}\nLink:\n{invite.invite_link}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode gerar links",
            )
        )
    except Exception:
        logger.exception("Falha ao criar link direto")
        await message.answer(
            _error_text(
                "falha ao criar link",
                "verifique o chat_id, permissões do bot e tente novamente",
            )
        )


@router.message(Command("mx2"))
async def mx2(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 2:
        await message.answer(
            "Título: Link com aprovação\n"
            "Descrição: Gera link onde a entrada depende de aprovação.\n\n"
            "Use:\n"
            "/mx2\n"
            "<chat_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        invite = await message.bot.create_chat_invite_link(
            chat_id=chat_id,
            creates_join_request=True,
        )
        _remember_group(chat_id, str(chat_id))
        await message.answer(
            _success_text(
                "Link de solicitação de entrada gerado.",
                f"Grupo: {chat_id}\nLink:\n{invite.invite_link}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode gerar links",
            )
        )
    except Exception:
        logger.exception("Falha ao criar link com aprovação")
        await message.answer(
            _error_text(
                "falha ao criar link",
                "verifique o chat_id, permissões do bot e tente novamente",
            )
        )


@router.message(Command("joinx"))
async def joinx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    _ensure_join_requests_table()

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Aprovação manual\n"
            "Descrição: Aprova usuário que solicitou entrada recentemente.\n\n"
            "Use:\n"
            "/joinx\n"
            "<chat_id>\n"
            "<user_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        user_id = _parse_user_id(lines[2])
    except Exception:
        await message.answer(
            _error_text(
                "chat_id ou user_id inválido",
                "envie apenas números nas linhas 2 e 3",
            )
        )
        return

    cutoff = datetime.now(timezone.utc) - APPROVAL_WINDOW

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM join_requests WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )

        row = (
            conn.execute(
                text(
                    """
                    SELECT user_id, chat_id, created_at
                    FROM join_requests
                    WHERE user_id = :user_id AND chat_id = :chat_id
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "user_id": user_id,
                    "chat_id": chat_id,
                },
            )
            .mappings()
            .first()
        )

    if not row:
        await message.answer(
            _error_text(
                "solicitação não encontrada",
                "confirme se o usuário solicitou entrada nos últimos 120 minutos",
            )
        )
        return

    created_at = _parse_created_at(row["created_at"])
    if created_at is None or created_at < cutoff:
        await message.answer(
            _error_text(
                "solicitação expirada",
                "peça para o usuário solicitar entrada novamente",
            )
        )
        return

    try:
        await message.bot.approve_chat_join_request(
            chat_id=chat_id,
            user_id=user_id,
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode aprovar solicitações",
            )
        )
        return
    except Exception:
        logger.exception("Falha na aprovação manual")
        await message.answer(
            _error_text(
                "falha na aprovação",
                "verifique os dados e tente novamente",
            )
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

    _remember_group(chat_id, str(chat_id))
    await message.answer(
        _success_text(
            "Usuário aprovado.",
            f"Grupo: {chat_id}\nUsuário: {user_id}",
        )
    )


@router.message(Command("vx"))
async def vx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Vanish\n"
            "Descrição: Remove usuário imediatamente do grupo.\n\n"
            "Use:\n"
            "/vx\n"
            "<chat_id>\n"
            "<user_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        user_id = _parse_user_id(lines[2])
        await _execute_action(message.bot, chat_id, user_id, "vanish")
        _remember_group(chat_id, str(chat_id))
        await _notify_owner(message.bot, chat_id, f"Vanish executado | user_id={user_id}")
        await message.answer(
            _success_text(
                "Vanish executado.",
                f"Grupo: {chat_id}\nUsuário: {user_id}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode banir usuários",
            )
        )
    except Exception:
        logger.exception("Falha no vanish")
        await message.answer(
            _error_text(
                "falha na execução",
                "verifique chat_id, user_id e permissões do bot",
            )
        )


@router.message(Command("uv"))
async def uv(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Unvanish\n"
            "Descrição: Restaura acesso de usuário removido.\n\n"
            "Use:\n"
            "/uv\n"
            "<chat_id>\n"
            "<user_id>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        user_id = _parse_user_id(lines[2])
        await _execute_action(message.bot, chat_id, user_id, "unvanish")
        _remember_group(chat_id, str(chat_id))
        await _notify_owner(message.bot, chat_id, f"Unvanish executado | user_id={user_id}")
        await message.answer(
            _success_text(
                "Unvanish executado.",
                f"Grupo: {chat_id}\nUsuário: {user_id}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode desbanir usuários",
            )
        )
    except Exception:
        logger.exception("Falha no unvanish")
        await message.answer(
            _error_text(
                "falha na execução",
                "verifique chat_id, user_id e permissões do bot",
            )
        )


@router.message(Command("mx"))
async def mx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Mute temporário\n"
            "Descrição: Silencia usuário por tempo definido. Se não informar tempo, usa 10 minutos.\n\n"
            "Use:\n"
            "/mx\n"
            "<chat_id>\n"
            "<user_id>\n"
            "<minutos opcional até 120>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        user_id = _parse_user_id(lines[2])
        duration_raw = lines[3] if len(lines) > 3 else "10m"

        try:
            parsed = _parse_duration(duration_raw)
        except Exception:
            await message.answer("Duração inválida. Use: 10m, 2h, 3d, i ou x")
            return

        if parsed == "unmute":
            await message.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )

            await message.answer(f"Usuário desmutado.\nUser: {user_id}")
            return

        if parsed == "indefinido":
            until = None
        else:
            until = datetime.now(timezone.utc) + parsed

        await message.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )

        await message.answer(
            f"Usuário silenciado.\nUser: {user_id}\nTempo: {duration_raw}"
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador do grupo e pode restringir usuários",
            )
        )
    except Exception:
        logger.exception("Falha ao aplicar mute")
        await message.answer(
            _error_text(
                "falha ao aplicar mute",
                "verifique chat_id, user_id, tempo e permissões do bot",
            )
        )


@router.message(Command("ovbx"))
async def ovbx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 4:
        await message.answer(
            "Título: Painel direto de moderação\n"
            "Descrição: Executa ação direta sem depender de sessão.\n\n"
            "Use:\n"
            "/ovbx\n"
            "<chat_id>\n"
            "<user_id>\n"
            "<vanish|unvanish|mute>\n"
            "<minutos opcional para mute>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        user_id = _parse_user_id(lines[2])
        action = lines[3].strip().lower()

        if action not in {"vanish", "unvanish", "mute"}:
            await message.answer(
                _error_text(
                    "ação inválida",
                    "use vanish, unvanish ou mute",
                )
            )
            return

        minutes = int(lines[4]) if action == "mute" and len(lines) >= 5 else 10
        minutes = max(1, min(minutes, 120))

        await _execute_action(message.bot, chat_id, user_id, action, minutes)
        _remember_group(chat_id, str(chat_id))
        await _notify_owner(message.bot, chat_id, f"Ação executada: {action} | user_id={user_id}")
        await message.answer(
            _success_text(
                "Ação executada.",
                f"Grupo: {chat_id}\nUsuário: {user_id}\nAção: {action}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador e possui permissões suficientes",
            )
        )
    except Exception:
        logger.exception("Falha no ovbx")
        await message.answer(
            _error_text(
                "falha na execução",
                "verifique chat_id, user_id, ação e permissões do bot",
            )
        )


@router.message(Command("lgx"))
async def lgx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Notificações privadas\n"
            "Descrição: Ativa ou desativa alertas privados para ações internas.\n\n"
            "Use:\n"
            "/lgx\n"
            "<chat_id>\n"
            "<on|off>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        value = lines[2].strip().lower()

        if value not in {"on", "off", "true", "false", "1", "0", "sim", "nao", "não"}:
            await message.answer(
                _error_text(
                    "valor inválido",
                    "use on ou off",
                )
            )
            return

        enabled = value in {"on", "true", "1", "sim"}
        _save_rule(chat_id, "notify", {"enabled": enabled})
        _remember_group(chat_id, str(chat_id))

        await message.answer(
            _success_text(
                "Notificações atualizadas.",
                f"Grupo: {chat_id}\nStatus: {'ativas' if enabled else 'inativas'}",
            )
        )
    except Exception:
        logger.exception("Falha ao salvar notificação lgx")
        await message.answer(
            _error_text(
                "falha ao salvar notificação",
                "verifique chat_id e valor on/off",
            )
        )


@router.message(Command("fdx"))
async def fdx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Busca\n"
            "Descrição: Busca registros internos de advertência.\n\n"
            "Use:\n"
            "/fdx\n"
            "<chat_id>\n"
            "<termo>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        term = f"%{lines[2].strip().lower()}%"

        _ensure_warns_table()

        with engine.begin() as conn:
            rows = (
                conn.execute(
                    text(
                        """
                        SELECT user_id, reason, created_at
                        FROM warns
                        WHERE chat_id = :chat_id AND lower(reason) LIKE :term
                        ORDER BY created_at DESC
                        LIMIT 20
                        """
                    ),
                    {
                        "chat_id": chat_id,
                        "term": term,
                    },
                )
                .mappings()
                .all()
            )

        if not rows:
            await message.answer("Nenhum resultado.")
            return

        await message.answer(
            "\n".join(
                f"{row['user_id']} | {row['reason']} | {row['created_at']}"
                for row in rows
            )
        )
    except Exception:
        logger.exception("Falha na busca fdx")
        await message.answer(
            _error_text(
                "falha na busca",
                "verifique chat_id e termo informado",
            )
        )


@router.message(Command("clx"))
async def clx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Título: Limpeza\n"
            "Descrição: Executa limpeza segura de registros internos.\n\n"
            "Use:\n"
            "/clx\n"
            "<chat_id>\n"
            "<warns|rules|old_requests>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        target = lines[2].strip().lower()

        if target not in {"warns", "rules", "old_requests"}:
            await message.answer(
                _error_text(
                    "tipo inválido",
                    "use warns, rules ou old_requests",
                )
            )
            return

        if target == "warns":
            _ensure_warns_table()
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM warns WHERE chat_id = :chat_id"),
                    {"chat_id": chat_id},
                )

        if target == "rules":
            _ensure_group_rules_table()
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM group_rules WHERE chat_id = :chat_id"),
                    {"chat_id": chat_id},
                )

        if target == "old_requests":
            _ensure_join_requests_table()
            cutoff = datetime.now(timezone.utc) - APPROVAL_WINDOW
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM join_requests WHERE created_at < :cutoff"),
                    {"cutoff": cutoff},
                )

        await message.answer(
            _success_text(
                "Limpeza executada.",
                f"Grupo: {chat_id}\nTipo: {target}",
            )
        )
    except Exception:
        logger.exception("Falha na limpeza clx")
        await message.answer(
            _error_text(
                "falha na limpeza",
                "verifique chat_id e tipo informado",
            )
        )




@router.message(Command("xend"))
async def xend(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Use:\n"
            "/xend\n"
            "<chat_id>\n"
            "<mensagem>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        text_message = "\n".join(lines[2:]).strip()

        # 🔹 Se respondeu alguma mensagem com mídia → copiar
        if message.reply_to_message:
            reply = message.reply_to_message

            try:
                await message.bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=message.chat.id,
                    message_id=reply.message_id,
                )

                await message.answer(
                    _success_text(
                        "Mensagem enviada (cópia).",
                        f"Destino: {chat_id}",
                    )
                )
                return

            except Exception:
                logger.exception("Falha ao copiar mensagem no xend")

        # 🔹 fallback texto
        if not text_message:
            await message.answer(
                _error_text(
                    "mensagem vazia",
                    "informe o conteúdo ou responda uma mensagem",
                )
            )
            return

        await message.bot.send_message(
            chat_id=chat_id,
            text=text_message,
        )

        await message.answer(
            _success_text(
                "Mensagem enviada.",
                f"Destino: {chat_id}",
            )
        )

    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot pode enviar mensagens para este chat",
            )
        )

    except Exception:
        logger.exception("Falha no comando xend")
        await message.answer(
            _error_text(
                "falha ao enviar mensagem",
                "verifique chat_id e conteúdo da mensagem",
            )
        )


@router.message(Command("ximg"))
async def ximg(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 2:
        await message.answer(
            "Use (respondendo uma imagem):\n"
            "/ximg\n"
            "<chat_id>"
        )
        return

    if not message.reply_to_message or not message.reply_to_message.photo:
        await message.answer(
            _error_text(
                "imagem não informada",
                "responda a uma mensagem com foto e use /ximg na resposta",
            )
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])

        photo = message.reply_to_message.photo[-1]
        if hasattr(photo, "file_size") and photo.file_size and photo.file_size > 10_000_000:
            await message.answer(
                _error_text(
                    "imagem muito grande",
                    "use imagem menor que 10MB",
                )
            )
            return

        file = await message.bot.get_file(photo.file_id)
        file_obj = await message.bot.download_file(file.file_path)
        photo_bytes = file_obj.read() if hasattr(file_obj, "read") else file_obj

        await message.bot.set_chat_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(photo_bytes, filename="chat_photo.jpg"),
        )
        await message.answer(
            _success_text(
                "Foto atualizada.",
                f"Grupo: {chat_id}",
            )
        )
    except TelegramForbiddenError:
        await message.answer(
            _error_text(
                "operação não permitida",
                "verifique se o bot é administrador e pode alterar foto do chat",
            )
        )
    except Exception:
        logger.exception("Falha no comando ximg")
        await message.answer(
            _error_text(
                "falha ao atualizar foto",
                "verifique chat_id, permissões e a imagem informada",
            )
        )

@router.message(Command("hidden"))
async def hidden(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    await message.answer(
        "COMANDOS ADMINISTRATIVOS\n\n"
        "MODERAÇÃO:\n"
        "/dxx\n<link> — apaga mensagem diretamente via link\n\n"
        "AÇÕES DIRETAS:\n"
        "/vx\n<chat_id>\n<user_id> — remover usuário (ban)\n\n"
        "/uv\n<chat_id>\n<user_id> — desbanir usuário\n\n"
        "/mx\n<chat_id>\n<user_id>\n[minutos] — silenciar usuário\n\n"
        "/ovbx\n<chat_id>\n<user_id>\n<vanish|unvanish|mute>\n[minutos] — ação manual completa\n\n"
        "ACESSO:\n"
        "/mx1\n<chat_id> — gerar link direto\n\n"
        "/mx2\n<chat_id> — gerar link com aprovação\n\n"
        "/joinx\n<chat_id>\n<user_id> — aprovar entrada manual\n\n"
        "GRUPOS:\n"
        "/addgroup\n<chat_id>\n[nome] — registrar grupo\n\n"
        "/groups — listar grupos\n\n"
        "SISTEMA:\n"
        "/hidden — exibir comandos\n"
    )
