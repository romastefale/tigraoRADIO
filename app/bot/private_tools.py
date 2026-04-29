from __future__ import annotations

import json
import logging
import unicodedata
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import BaseFilter, Command
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


def _ensure_dxx_filters_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS dxx_filters (
                    chat_id INTEGER,
                    word TEXT,
                    action TEXT,
                    updated_at DATETIME,
                    PRIMARY KEY (chat_id, word)
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
    _ensure_dxx_filters_table()
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
        return dict(json.loads(str(row["payload"])))
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


def _normalize_words(raw: str) -> list[str]:
    normalized = raw.replace(";", ",").replace("\n", ",")
    words = [item.strip().lower() for item in normalized.split(",") if item.strip()]
    return list(dict.fromkeys(words))


def _save_dxx_filters(chat_id: int, action: str, words: list[str]) -> None:
    _ensure_dxx_filters_table()

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM dxx_filters WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        )
        for word in words:
            conn.execute(
                text(
                    """
                    INSERT INTO dxx_filters (chat_id, word, action, updated_at)
                    VALUES (:chat_id, :word, :action, :updated_at)
                    """
                ),
                {
                    "chat_id": chat_id,
                    "word": word,
                    "action": action,
                    "updated_at": datetime.now(timezone.utc),
                },
            )


def _get_dxx_filters(chat_id: int) -> tuple[str | None, list[str]]:
    _ensure_dxx_filters_table()

    with engine.begin() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT word, action
                    FROM dxx_filters
                    WHERE chat_id = :chat_id
                    ORDER BY word ASC
                    """
                ),
                {"chat_id": chat_id},
            )
            .mappings()
            .all()
        )

    if not rows:
        return None, []

    words = [str(row["word"]).strip().lower() for row in rows if row.get("word")]
    action = str(rows[0].get("action") or "").strip().lower() or None
    return action, list(dict.fromkeys(words))


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
        until = datetime.now(timezone.utc) + timedelta(
            minutes=max(1, min(duration_minutes or 10, 120))
        )
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


def _format_words_rule(chat_id: int) -> str:
    payload = _get_rule(chat_id, "words")
    if not payload:
        return "Nenhuma regra /dxx salva para este grupo."

    words = payload.get("words", [])
    action = str(payload.get("action") or "").strip().lower()
    if not isinstance(words, list) or not words:
        return "Nenhuma regra /dxx salva para este grupo."

    return (
        "DXX FILTERS\n"
        f"Grupo: {chat_id}\n"
        f"Ação: {action or 'não definida'}\n"
        f"Palavras: {', '.join(str(word) for word in words)}"
    )


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


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")




class DxxWordFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        text_value = message.text or message.caption
        if not text_value or not message.from_user:
            return False

        payload = _get_rule(message.chat.id, "words")
        if not payload:
            return False

        words = payload.get("words", [])
        if not isinstance(words, list) or not words:
            return False

        text_norm = _normalize_text(text_value)
        return any(_normalize_text(str(word)) in text_norm for word in words)

@router.message(F.chat.type.in_({"group", "supergroup"}), DxxWordFilter())
async def handle_group_word_filter(message: Message) -> None:
    _remember_group(message.chat.id, message.chat.title or str(message.chat.id))

    chat_id = message.chat.id
    payload = _get_rule(chat_id, "words")
    if not payload:
        return

    action = str(payload.get("action") or "").strip().lower()
    if action not in {"delete", "vanish"}:
        return

    if not message.from_user:
        return

    try:
        member = await message.bot.get_chat_member(chat_id, message.from_user.id)
    except Exception:
        logger.exception("Falha ao verificar admin no grupo %s", chat_id)
        return

    if member.status in {"administrator", "creator"}:
        return

    try:
        if action == "delete":
            await message.delete()
            return

        await _execute_action(
            message.bot,
            chat_id,
            message.from_user.id,
            "vanish",
        )
    except TelegramForbiddenError:
        logger.exception("Sem permissão no grupo %s", chat_id)
    except Exception:
        logger.exception(
            "Erro crítico no filtro de palavras | chat_id=%s | user_id=%s",
            chat_id,
            getattr(message.from_user, "id", None),
        )


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

    if len(lines) < 4:
        await message.answer(
            "Título: Filtro rápido\n"
            "Descrição: Define palavras proibidas com ação direta.\n\n"
            "Use:\n"
            "/dxx\n"
            "<chat_id>\n"
            "<delete|vanish>\n"
            "<palavras separadas por vírgula ou linha>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        action = lines[2].strip().lower()

        if action not in {"delete", "vanish"}:
            await message.answer(
                _error_text(
                    "ação inválida",
                    "use delete ou vanish",
                )
            )
            return

        raw_words = "\n".join(lines[3:])
        words = _normalize_words(raw_words)

        if not words:
            await message.answer(
                _error_text(
                    "nenhuma palavra válida",
                    "informe ao menos uma palavra",
                )
            )
            return

        _save_rule(
            chat_id,
            "words",
            {
                "words": words,
                "action": action,
            },
        )

        _remember_group(chat_id, str(chat_id))

        await message.answer(
            _success_text(
                "Filtro configurado.",
                f"Grupo: {chat_id}\nAção: {action}\nPalavras: {len(words)}",
            )
        )

    except Exception:
        logger.exception("Falha no comando dxx")
        await message.answer(
            _error_text(
                "erro ao configurar filtro",
                "verifique os dados e tente novamente",
            )
        )


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

    await message.answer(_format_words_rule(chat_id))


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
        minutes = int(lines[3]) if len(lines) >= 4 else 10
        minutes = max(1, min(minutes, 120))

        await _execute_action(message.bot, chat_id, user_id, "mute", minutes)
        _remember_group(chat_id, str(chat_id))
        await _notify_owner(message.bot, chat_id, f"Mute executado | user_id={user_id} | minutos={minutes}")
        await message.answer(
            _success_text(
                "Mute executado.",
                f"Grupo: {chat_id}\nUsuário: {user_id}\nTempo: {minutes} minutos",
            )
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
        "COMANDOS ATIVOS\n\n"
        "MODERAÇÃO:\n"
        "/dxx\n<chat_id>\n<delete|vanish>\n<palavras> — filtro automático de palavras\n\n"
        "/vx\n<chat_id>\n<user_id> — vanish\n\n"
        "/uv\n<chat_id>\n<user_id> — unvanish\n\n"
        "/mx\n<chat_id>\n<user_id>\n[minutos] — mute\n\n"
        "/ovbx\n<chat_id>\n<user_id>\n<vanish|unvanish|mute>\n[minutos] — ação direta\n\n"
        "ACESSO:\n"
        "/mx1\n<chat_id> — link direto\n\n"
        "/mx2\n<chat_id> — link com aprovação\n\n"
        "/joinx\n<chat_id>\n<user_id> — aprovar solicitação\n\n"
        "GRUPOS:\n"
        "/addgroup\n<chat_id>\n[nome] — registrar grupo\n\n"
        "/groups — listar grupos\n\n"
        "/rules\n<chat_id> — listar filtro /dxx\n\n"
        "/xend\n<chat_id>\n<mensagem> — enviar mensagem\n\n"
        "/ximg\n<chat_id> (responder imagem) — alterar foto\n\n"
        "Sistema:\n"
        "/hidden"
    )
