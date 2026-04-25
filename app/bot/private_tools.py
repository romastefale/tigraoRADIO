from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import text

from app.db.database import engine


OWNER_ID = 8505890439
APPROVAL_WINDOW = timedelta(hours=2)
SINGLE_USE_EXPIRY = timedelta(minutes=5)

SESSIONS: dict[int, dict[str, object]] = {}

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
                    SELECT chat_id, title
                    FROM known_groups
                    ORDER BY updated_at DESC
                    """
                )
            )
            .mappings()
            .all()
        )

    return [dict(row) for row in rows]


def _group_keyboard() -> InlineKeyboardMarkup:
    groups = _get_known_groups()

    if not groups:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Nenhum grupo registrado",
                        callback_data="pt|nogroups",
                    )
                ]
            ]
        )

    rows: list[list[InlineKeyboardButton]] = []
    current: list[InlineKeyboardButton] = []

    for group in groups:
        chat_id = int(group["chat_id"])
        title = str(group.get("title") or chat_id)

        current.append(
            InlineKeyboardButton(
                text=title[:48],
                callback_data=f"pt|grp|{chat_id}",
            )
        )

        if len(current) == 2:
            rows.append(current)
            current = []

    if current:
        rows.append(current)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    def label(action: str, text_value: str) -> str:
        return f"☑ {text_value}" if selected == action else text_value

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label("vanish", "☒ Vanish"), callback_data="pt|action|vanish"),
                InlineKeyboardButton(text=label("unvanish", "⚀ Unvanish"), callback_data="pt|action|unvanish"),
            ],
            [
                InlineKeyboardButton(text=label("mute", "☊ Mute"), callback_data="pt|action|mute"),
                InlineKeyboardButton(text=label("warn", "⚠ Warn"), callback_data="pt|action|warn"),
            ],
            [
                InlineKeyboardButton(text="☑ Salvar", callback_data="pt|save"),
            ],
        ]
    )


def _punishment_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    def label(action: str, text_value: str) -> str:
        return f"☑ {text_value}" if selected == action else text_value

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label("vanish", "☒ Vanish"), callback_data="pt|fwx_action|vanish"),
                InlineKeyboardButton(text=label("mute", "☊ Mute"), callback_data="pt|fwx_action|mute"),
            ],
            [
                InlineKeyboardButton(text=label("warn", "⚠ Warn"), callback_data="pt|fwx_action|warn"),
                InlineKeyboardButton(text="☑ Salvar", callback_data="pt|fwx_save"),
            ],
        ]
    )


def _duration_keyboard(selected: int | None = None) -> InlineKeyboardMarkup:
    def label(minutes: int, text_value: str) -> str:
        return f"☑ {text_value}" if selected == minutes else text_value

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label(5, "5m"), callback_data="pt|duration|5"),
                InlineKeyboardButton(text=label(10, "10m"), callback_data="pt|duration|10"),
            ],
            [
                InlineKeyboardButton(text=label(30, "30m"), callback_data="pt|duration|30"),
                InlineKeyboardButton(text=label(60, "1h"), callback_data="pt|duration|60"),
            ],
            [
                InlineKeyboardButton(text=label(120, "2h"), callback_data="pt|duration|120"),
                InlineKeyboardButton(text="☑ Salvar", callback_data="pt|save"),
            ],
        ]
    )


def _yes_no_keyboard(prefix: str, selected: bool | None = None) -> InlineKeyboardMarkup:
    yes_label = "☑ Sim" if selected is True else "Sim"
    no_label = "☑ Não" if selected is False else "Não"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_label, callback_data=f"pt|{prefix}|1"),
                InlineKeyboardButton(text=no_label, callback_data=f"pt|{prefix}|0"),
            ],
            [
                InlineKeyboardButton(text="☑ Salvar", callback_data=f"pt|{prefix}_save"),
            ],
        ]
    )


def _fwx_mode_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    add_label = "☑ Adicionar" if selected == "add" else "Adicionar"
    remove_label = "☑ Remover" if selected == "remove" else "Remover"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=add_label, callback_data="pt|fwx_mode|add"),
                InlineKeyboardButton(text=remove_label, callback_data="pt|fwx_mode|remove"),
            ]
        ]
    )


def _clean_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    def label(key: str, text_value: str) -> str:
        return f"☑ {text_value}" if selected == key else text_value

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label("muted", "Mutados"), callback_data="pt|clx_type|muted"),
                InlineKeyboardButton(text=label("banned", "Banidos"), callback_data="pt|clx_type|banned"),
            ],
            [
                InlineKeyboardButton(text=label("deleted", "Deletados"), callback_data="pt|clx_type|deleted"),
                InlineKeyboardButton(text="☑ Salvar", callback_data="pt|clx_save"),
            ],
        ]
    )


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
        return None


def _notify_enabled(chat_id: int) -> bool:
    payload = _get_rule(chat_id, "notify")
    if not payload:
        return False
    return bool(payload.get("enabled"))


async def _notify_owner(bot, chat_id: int, text_message: str) -> None:
    if not _notify_enabled(chat_id):
        return

    await bot.send_message(chat_id=OWNER_ID, text=text_message)


def _error_text(reason: str, fix: str) -> str:
    return f"Erro:\nMotivo: {reason}\nComo corrigir: {fix}"


def _start_session(user_id: int, flow: str, step: str = "choose_group") -> None:
    SESSIONS[user_id] = {
        "flow": flow,
        "chat_id": None,
        "target_user_id": None,
        "action": None,
        "duration": None,
        "step": step,
        "payload": {},
    }


def _clear_session(user_id: int) -> None:
    SESSIONS.pop(user_id, None)


def _require_session(user_id: int) -> dict[str, object] | None:
    return SESSIONS.get(user_id)


def _normalize_words(raw: str) -> list[str]:
    words = [item.strip().lower() for item in re.split(r"[,;\n]", raw) if item.strip()]
    return list(dict.fromkeys(words))


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


async def _execute_action(bot, chat_id: int, user_id: int, action: str, duration_minutes: int | None = None) -> None:
    if action == "vanish":
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        return

    if action == "unvanish":
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        return

    if action == "mute":
        until = datetime.now(timezone.utc) + timedelta(minutes=max(1, min(duration_minutes or 10, 120)))
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


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def _auto_register_group(message: Message) -> None:
    _remember_group(message.chat.id, message.chat.title)


async def _send_group_selection(message: Message, flow: str, title: str, description: str) -> None:
    _start_session(message.from_user.id, flow)

    await message.answer(
        f"Título: {title}\n"
        f"Descrição: {description}\n\n"
        "Selecione o grupo:",
        reply_markup=_group_keyboard(),
    )


@router.message(Command("mx1"))
async def mx1(message: Message) -> None:
    await _send_group_selection(message, "mx1", "Link direto", "Gera link de entrada imediata.")


@router.message(Command("mx2"))
async def mx2(message: Message) -> None:
    await _send_group_selection(message, "mx2", "Link com aprovação", "Gera link com aprovação.")


@router.message(Command("joinx"))
async def joinx(message: Message) -> None:
    await _send_group_selection(message, "joinx", "Aprovação manual", "Aprova usuário pendente.")


@router.message(Command("hidden"))
async def hidden(message: Message) -> None:
    await message.answer("COMANDOS OCULTOS")


@router.callback_query(F.data.startswith("pt|"))
async def private_tools_callbacks(callback: CallbackQuery) -> None:
    if callback.from_user.id != OWNER_ID:
        return

    if callback.message is None or callback.message.chat.type != "private":
        return

    parts = (callback.data or "").split("|")
    uid = callback.from_user.id
    session = _require_session(uid)

    if not session:
        return

    if parts[1] == "grp":
        chat_id = int(parts[2])
        session["chat_id"] = chat_id

        if session["flow"] == "mx1":
            invite = await callback.bot.create_chat_invite_link(
                chat_id=chat_id,
                creates_join_request=False,
                member_limit=1,
                expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
            )
            await callback.message.edit_text(invite.invite_link)

        _clear_session(uid)


@router.message(F.text.regexp(r"^\d+$"))
async def handle_numeric_input(message: Message) -> None:
    pass


@router.message(F.chat.type == "private")
async def handle_text_input(message: Message) -> None:
    pass
