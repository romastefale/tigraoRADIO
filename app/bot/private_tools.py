from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    ChatJoinRequest,
    ChatMemberUpdated,
    ChatPermissions,
    Message,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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


def _ensure_ddx_rules_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ddx_rules (
                    chat_id INTEGER PRIMARY KEY,
                    words TEXT,
                    enabled INTEGER,
                    updated_at DATETIME
                );
                """
            )
        )


def _ensure_known_chats_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS known_chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    updated_at DATETIME
                );
                """
            )
        )


def _ensure_all_tables() -> None:
    _ensure_join_requests_table()
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


def _parse_message_link(link: str) -> tuple[int, int]:
    match = re.search(r"/c/(\d+)/(\d+)", link.strip())
    if not match:
        raise ValueError("link inválido")

    chat_id = int("-100" + match.group(1))
    message_id = int(match.group(2))

    return chat_id, message_id


def _ddx_normalize_spaced(value: str) -> str:
    value = value.lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _ddx_normalize_compact(value: str) -> str:
    value = value.lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def _ddx_parse_words(raw: str) -> list[str]:
    words = [item.strip() for item in re.split(r"[,;\n]", raw) if item.strip()]
    normalized = []
    seen = set()
    for word in words:
        clean = _ddx_normalize_spaced(word)
        if not clean:
            continue
        if clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized


def _save_known_chat(chat_id: int, title: str) -> None:
    _ensure_known_chats_table()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO known_chats (chat_id, title, updated_at)
                VALUES (:chat_id, :title, :updated_at)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "chat_id": chat_id,
                "title": title,
                "updated_at": datetime.now(timezone.utc),
            },
        )


def _list_known_chats(limit: int = 10) -> list[tuple[int, str]]:
    _ensure_known_chats_table()
    with engine.begin() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT chat_id, title
                    FROM known_chats
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    return [(int(r["chat_id"]), str(r["title"])) for r in rows]


def _ddx_save(chat_id: int, words: list[str], enabled: bool = True) -> None:
    _ensure_ddx_rules_table()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ddx_rules (chat_id, words, enabled, updated_at)
                VALUES (:chat_id, :words, :enabled, :updated_at)
                ON CONFLICT(chat_id) DO UPDATE SET
                    words = excluded.words,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "chat_id": chat_id,
                "words": json.dumps(words, ensure_ascii=False),
                "enabled": 1 if enabled else 0,
                "updated_at": datetime.now(timezone.utc),
            },
        )


def _ddx_get(chat_id: int) -> dict[str, object] | None:
    _ensure_ddx_rules_table()
    with engine.begin() as conn:
        row = (
            conn.execute(
                text(
                    """
                    SELECT words, enabled
                    FROM ddx_rules
                    WHERE chat_id = :chat_id
                    """
                ),
                {"chat_id": chat_id},
            )
            .mappings()
            .first()
        )
    if not row:
        return None
    try:
        words = json.loads(str(row["words"] or "[]"))
        if not isinstance(words, list):
            words = []
    except Exception:
        logger.exception("DDX_LOAD_FAILED | chat_id=%s", chat_id)
        words = []
    return {"words": words, "enabled": bool(row["enabled"])}


def _ddx_match(text_value: str, words: list[str]) -> bool:
    spaced_text = _ddx_normalize_spaced(text_value)
    compact_text = _ddx_normalize_compact(text_value)
    for word in words:
        spaced_word = _ddx_normalize_spaced(str(word))
        compact_word = _ddx_normalize_compact(str(word))
        if not spaced_word or not compact_word:
            continue
        if " " in spaced_word:
            if spaced_word in spaced_text:
                return True
        else:
            if spaced_word in spaced_text or compact_word in compact_text:
                return True
    return False


_ddx_user_state: dict[int, dict[str, object]] = {}
_DDX_STATE_TTL = 300


def _set_state(user_id: int, data: dict[str, object]) -> None:
    data["ts"] = int(datetime.now(timezone.utc).timestamp())
    _ddx_user_state[user_id] = data


def _get_state(user_id: int) -> dict[str, object] | None:
    data = _ddx_user_state.get(user_id)
    if not data:
        return None
    ts = int(data.get("ts", 0))
    now = int(datetime.now(timezone.utc).timestamp())
    if now - ts > _DDX_STATE_TTL:
        _ddx_user_state.pop(user_id, None)
        return None
    return data


def _clear_state(user_id: int) -> None:
    _ddx_user_state.pop(user_id, None)



async def ddx_preprocess_update(bot, update) -> bool:
    message = getattr(update, "message", None) or getattr(update, "edited_message", None)

    if not message:
        return False

    if message.chat.type not in {"group", "supergroup"}:
        return False

    text_value = message.text or message.caption
    if not text_value:
        return False

    if not message.from_user or message.from_user.is_bot:
        return False

    payload = _ddx_get(message.chat.id)
    if not payload or not payload.get("enabled"):
        return False

    words = payload.get("words", [])
    if not isinstance(words, list) or not words:
        return False

    if not _ddx_match(text_value, words):
        return False

    try:
        member = await bot.get_chat_member(
            message.chat.id,
            message.from_user.id,
        )

        if member.status in {"administrator", "creator"}:
            logger.warning(
                "DDX_SKIP_ADMIN | chat_id=%s | user_id=%s | message_id=%s",
                message.chat.id,
                message.from_user.id,
                message.message_id,
            )
            return False

    except Exception:
        logger.exception(
            "DDX_ADMIN_CHECK_FAILED | chat_id=%s | user_id=%s | message_id=%s",
            message.chat.id,
            getattr(message.from_user, "id", None),
            message.message_id,
        )
        return False

    try:
        await bot.delete_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
        )

        logger.warning(
            "DDX_DELETED | chat_id=%s | user_id=%s | message_id=%s",
            message.chat.id,
            message.from_user.id,
            message.message_id,
        )

        return True

    except Exception:
        logger.exception(
            "DDX_DELETE_FAILED | chat_id=%s | user_id=%s | message_id=%s",
            message.chat.id,
            getattr(message.from_user, "id", None),
            message.message_id,
        )
        return False


@router.message(Command("ddx"))
async def ddx(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    lines = _lines(message)
    if len(lines) < 3:
        await message.answer(
            "Use:\n"
            "/ddx\n"
            "<chat_id>\n"
            "<add|remove|list|off|test>\n"
            "<palavras ou texto>"
        )
        return

    try:
        chat_id = _parse_chat_id(lines[1])
        mode = lines[2].strip().lower()
        current = _ddx_get(chat_id) or {"words": [], "enabled": True}
        current_words = current.get("words", [])
        if not isinstance(current_words, list):
            current_words = []

        if mode == "list":
            await message.answer(
                "DDX\n"
                f"Grupo: {chat_id}\n"
                f"Status: {'ativo' if current.get('enabled') else 'inativo'}\n"
                f"Palavras: {', '.join(str(word) for word in current_words) if current_words else 'nenhuma'}"
            )
            return

        if mode == "off":
            _ddx_save(chat_id, current_words, enabled=False)
            await message.answer(f"DDX desligado.\nGrupo: {chat_id}")
            return

        if mode == "add":
            if len(lines) < 4:
                await message.answer("Informe as palavras para adicionar.")
                return
            incoming = _ddx_parse_words("\n".join(lines[3:]))
            if not incoming:
                await message.answer("Nenhuma palavra válida informada.")
                return
            final_words = list(dict.fromkeys([str(w) for w in current_words] + incoming))
            _ddx_save(chat_id, final_words, enabled=True)
            await message.answer(
                "DDX atualizado.\n"
                f"Grupo: {chat_id}\n"
                "Status: ativo\n"
                f"Total de palavras: {len(final_words)}"
            )
            return

        if mode == "remove":
            if len(lines) < 4:
                await message.answer("Informe as palavras para remover.")
                return
            remove_words = set(_ddx_parse_words("\n".join(lines[3:])))
            final_words = [str(w) for w in current_words if str(w) not in remove_words]
            _ddx_save(chat_id, final_words, enabled=True)
            await message.answer(
                "DDX atualizado.\n"
                f"Grupo: {chat_id}\n"
                "Status: ativo\n"
                f"Total de palavras: {len(final_words)}"
            )
            return

        if mode == "test":
            if len(lines) < 4:
                await message.answer("Informe o texto para teste.")
                return
            test_text = "\n".join(lines[3:])
            matched = _ddx_match(test_text, [str(w) for w in current_words])
            await message.answer(
                "DDX TESTE\n"
                f"Grupo: {chat_id}\n"
                f"Resultado: {'detectado' if matched else 'não detectado'}"
            )
            return

        await message.answer("Modo inválido. Use add, remove, list, off ou test.")
    except Exception:
        logger.exception("DDX_COMMAND_FAILED")
        await message.answer("Erro ao processar /ddx.")


@router.message(Command("dxmenu"))
async def dxmenu(message: Message):
    if not _is_owner_private_message(message):
        return

    chats = _list_known_chats(limit=10)
    if not chats:
        await message.answer("Nenhum grupo conhecido ainda. Interaja no grupo primeiro.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=title[:30], callback_data=f"ddx:select:{chat_id}")]
            for chat_id, title in chats
        ]
    )
    await message.answer("Escolha o grupo:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("ddx:select:"))
async def ddx_select_group(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    chat_id = int(call.data.split(":")[2])
    _set_state(call.from_user.id, {"chat_id": chat_id})

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Adicionar", callback_data="ddx:act:add")],
            [InlineKeyboardButton(text="➖ Remover", callback_data="ddx:act:remove")],
            [InlineKeyboardButton(text="📋 Listar", callback_data="ddx:act:list")],
            [InlineKeyboardButton(text="🧪 Testar", callback_data="ddx:act:test")],
            [InlineKeyboardButton(text="⛔ Desligar", callback_data="ddx:act:off")],
        ]
    )

    await call.message.answer("Escolha a ação:", reply_markup=keyboard)
    await call.answer()


@router.callback_query(F.data.startswith("ddx:act:"))
async def ddx_select_action(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        return

    action = call.data.split(":")[2]
    state = _get_state(call.from_user.id)
    if not state:
        await call.message.answer("Sessão expirada. Use /dxmenu novamente.")
        await call.answer()
        return

    state["action"] = action
    _set_state(call.from_user.id, state)

    if action in {"add", "remove"}:
        await call.message.answer("Envie apenas as palavras (ex: pipizinho, spam)")
    elif action == "list":
        await call.message.answer("Listando…")
    elif action == "test":
        await call.message.answer("Envie o texto para teste")
    elif action == "off":
        await call.message.answer("Confirmando desligamento…")

    await call.answer()


@router.message()
async def ddx_menu_input_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return

    state = _get_state(message.from_user.id)
    if not state:
        return

    chat_id = int(state.get("chat_id"))
    action = str(state.get("action", ""))

    current = _ddx_get(chat_id) or {"words": [], "enabled": True}
    current_words = current.get("words", []) if isinstance(current.get("words"), list) else []

    if action == "add":
        words = _ddx_parse_words(message.text or "")
        if not words:
            await message.answer("Nenhuma palavra válida.")
            return
        final_words = list(dict.fromkeys([str(w) for w in current_words] + words))
        _ddx_save(chat_id, final_words, True)
        await message.answer(f"Adicionado. Total: {len(final_words)}")
        _clear_state(message.from_user.id)

    elif action == "remove":
        words = set(_ddx_parse_words(message.text or ""))
        final_words = [str(w) for w in current_words if str(w) not in words]
        _ddx_save(chat_id, final_words, True)
        await message.answer(f"Atualizado. Total: {len(final_words)}")
        _clear_state(message.from_user.id)

    elif action == "list":
        await message.answer(
            f"Palavras:\n{', '.join(current_words) if current_words else 'nenhuma'}"
        )
        _clear_state(message.from_user.id)

    elif action == "test":
        matched = _ddx_match(message.text or "", [str(w) for w in current_words])
        await message.answer("detectado" if matched else "não detectado")
        _clear_state(message.from_user.id)

    elif action == "off":
        _ddx_save(chat_id, current_words, False)
        await message.answer("DDX desligado")
        _clear_state(message.from_user.id)


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
        "/dx\n<link_da_mensagem>\n[outros links opcionais] — apaga mensagens diretamente por link\n\n"
        "/ddx\n<chat_id>\n<add|remove|list|off|test>\n<palavras ou texto> — filtro automático silencioso por palavras\n\n"
        "AÇÕES DIRETAS:\n"
        "/vx\n<chat_id>\n<user_id> — remover usuário (ban)\n\n"
        "/uv\n<chat_id>\n<user_id> — desbanir usuário\n\n"
        "/mx\n<chat_id>\n<user_id>\n<tempo>\n"
        "— silenciar usuário\n"
        "Formatos:\n"
        "10m = minutos\n"
        "2h = horas\n"
        "3d = dias\n"
        "i = indefinido\n"
        "x = desmutar\n\n"
        "ACESSO:\n"
        "/mx1\n<chat_id> — gerar link direto\n\n"
        "/mx2\n<chat_id> — gerar link com aprovação\n\n"
        "/joinx\n<chat_id>\n<user_id> — aprovar entrada manual\n\n"
        "SISTEMA:\n"
        "/hidden — exibir comandos\n"
    )
