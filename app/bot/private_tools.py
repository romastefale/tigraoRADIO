from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
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


def _is_owner_private_callback(callback: CallbackQuery) -> bool:
    return bool(
        callback.from_user
        and callback.from_user.id == OWNER_ID
        and callback.message
        and callback.message.chat.type == "private"
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
                InlineKeyboardButton(
                    text=label("vanish", "☒ Vanish"),
                    callback_data="pt|action|vanish",
                ),
                InlineKeyboardButton(
                    text=label("unvanish", "⚀ Unvanish"),
                    callback_data="pt|action|unvanish",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("mute", "☊ Mute"),
                    callback_data="pt|action|mute",
                ),
                InlineKeyboardButton(
                    text=label("warn", "⚠ Warn"),
                    callback_data="pt|action|warn",
                ),
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
                InlineKeyboardButton(
                    text=label("vanish", "☒ Vanish"),
                    callback_data="pt|fwx_action|vanish",
                ),
                InlineKeyboardButton(
                    text=label("mute", "☊ Mute"),
                    callback_data="pt|fwx_action|mute",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("warn", "⚠ Warn"),
                    callback_data="pt|fwx_action|warn",
                ),
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
                InlineKeyboardButton(
                    text=label("muted", "Mutados"),
                    callback_data="pt|clx_type|muted",
                ),
                InlineKeyboardButton(
                    text=label("banned", "Banidos"),
                    callback_data="pt|clx_type|banned",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("deleted", "Deletados"),
                    callback_data="pt|clx_type|deleted",
                ),
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


async def _execute_action(
    bot,
    chat_id: int,
    user_id: int,
    action: str,
    duration_minutes: int | None = None,
) -> None:
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
    try:
        _remember_group(message.chat.id, message.chat.title or str(message.chat.id))
    finally:
        raise SkipHandler()


async def _send_group_selection(message: Message, flow: str, title: str, description: str) -> None:
    if not _is_owner_private_message(message):
        return

    _start_session(message.from_user.id, flow)

    await message.answer(
        f"Título: {title}\n"
        f"Descrição: {description}\n\n"
        "Selecione o grupo:",
        reply_markup=_group_keyboard(),
    )


@router.message(Command("addgroup"))
async def addgroup(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(
            _error_text(
                "formato incorreto",
                "use /addgroup <chat_id> ou /addgroup <chat_id> <nome>",
            )
        )
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await message.answer(
            _error_text(
                "chat_id inválido",
                "envie um número válido, exemplo: /addgroup -1001234567890",
            )
        )
        return

    manual_title = parts[2].strip() if len(parts) >= 3 else None

    try:
        chat = await message.bot.get_chat(chat_id)
        _remember_group(chat.id, chat.title or manual_title or str(chat_id))
        await message.answer(f"Grupo registrado:\n{chat.title or manual_title or chat_id}")
    except Exception:
        if manual_title:
            _remember_group(chat_id, manual_title)
            await message.answer(f"Grupo registrado manualmente:\n{manual_title}")
            return

        await message.answer(
            _error_text(
                "não foi possível acessar o grupo",
                "verifique se o bot está no grupo ou use /addgroup <chat_id> <nome>",
            )
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


@router.message(Command("ovbx"))
async def ovbx(message: Message) -> None:
    await _send_group_selection(message, "ovbx", "Painel de moderação", "Executa ações diretas em um usuário.")


@router.message(Command("vx"))
async def vx(message: Message) -> None:
    await _send_group_selection(message, "vx", "Vanish", "Remove usuário imediatamente do grupo.")


@router.message(Command("uv"))
async def uv(message: Message) -> None:
    await _send_group_selection(message, "uv", "Unvanish", "Restaura acesso de usuário removido.")


@router.message(Command("wx"))
async def wx(message: Message) -> None:
    await _send_group_selection(message, "wx", "Warn", "Registra advertência sem remover usuário.")


@router.message(Command("mx"))
async def mx(message: Message) -> None:
    await _send_group_selection(message, "mx", "Mute temporário", "Silencia usuário por tempo definido.")


@router.message(Command("fwx"))
async def fwx(message: Message) -> None:
    await _send_group_selection(message, "fwx", "Filtro de palavras", "Define palavras proibidas e punição.")


@router.message(Command("lgx"))
async def lgx(message: Message) -> None:
    await _send_group_selection(message, "lgx", "Notificações privadas", "Ativa ou desativa alertas no privado.")


@router.message(Command("clx"))
async def clx(message: Message) -> None:
    await _send_group_selection(message, "clx", "Limpeza", "Executa rotinas internas de limpeza.")


@router.message(Command("fdx"))
async def fdx(message: Message) -> None:
    await _send_group_selection(message, "fdx", "Busca", "Consulta registros internos do grupo.")


@router.message(Command("hidden"))
async def hidden(message: Message) -> None:
    if not _is_owner_private_message(message):
        return

    await message.answer(
        "COMANDOS OCULTOS\n\n"
        "Membros:\n"
        "/mx1 — link direto\n"
        "/mx2 — link com aprovação\n"
        "/joinx — aprovar usuário\n"
        "/addgroup <chat_id> [nome] — registrar grupo manualmente\n\n"
        "Painel:\n"
        "/ovbx — painel geral\n"
        "/mx — mute com tempo\n"
        "/wx — warn\n"
        "/vx — vanish\n"
        "/uv — unvanish\n\n"
        "Regras:\n"
        "/fwx — palavras\n"
        "/clx — limpeza\n"
        "/lgx — notificações\n"
        "/fdx — busca\n\n"
        "Debug:\n"
        "/debuguser — auditoria completa\n\n"
        "Sistema:\n"
        "/healthfull — diagnóstico completo"
    )


@router.callback_query(F.data.startswith("pt|"))
async def private_tools_callbacks(callback: CallbackQuery) -> None:
    if not _is_owner_private_callback(callback):
        await callback.answer()
        return

    parts = (callback.data or "").split("|")
    if len(parts) < 2:
        await callback.answer()
        return

    uid = callback.from_user.id
    session = _require_session(uid)

    if parts[1] == "nogroups":
        await callback.message.answer(
            _error_text(
                "não há grupos registrados",
                "use /addgroup <chat_id> <nome> no privado para registrar manualmente",
            )
        )
        await callback.answer()
        return

    if not session:
        await callback.answer("Sessão expirada.", show_alert=True)
        return

    if parts[1] == "grp":
        if len(parts) < 3:
            await callback.answer("Grupo inválido.", show_alert=True)
            return

        chat_id = int(parts[2])
        session["chat_id"] = chat_id

        flow = str(session.get("flow") or "")

        if flow == "mx1":
            try:
                invite = await callback.bot.create_chat_invite_link(
                    chat_id=chat_id,
                    creates_join_request=False,
                    member_limit=1,
                    expire_date=datetime.now(timezone.utc) + SINGLE_USE_EXPIRY,
                )
                await callback.message.edit_text(invite.invite_link)
            except TelegramForbiddenError:
                await callback.message.edit_text(
                    _error_text(
                        "operação não permitida",
                        "verifique se o bot é administrador do grupo",
                    )
                )
            except Exception:
                await callback.message.edit_text(
                    _error_text(
                        "falha ao criar link",
                        "verifique as permissões do bot e tente novamente",
                    )
                )
            _clear_session(uid)
            await callback.answer()
            return

        if flow == "mx2":
            try:
                invite = await callback.bot.create_chat_invite_link(
                    chat_id=chat_id,
                    creates_join_request=True,
                )
                await callback.message.edit_text(invite.invite_link)
            except TelegramForbiddenError:
                await callback.message.edit_text(
                    _error_text(
                        "operação não permitida",
                        "verifique se o bot é administrador do grupo",
                    )
                )
            except Exception:
                await callback.message.edit_text(
                    _error_text(
                        "falha ao criar link",
                        "verifique as permissões do bot e tente novamente",
                    )
                )
            _clear_session(uid)
            await callback.answer()
            return

        if flow in {"joinx", "ovbx", "vx", "uv", "wx", "mx"}:
            fixed_action = {
                "vx": "vanish",
                "uv": "unvanish",
                "wx": "warn",
            }.get(flow)

            if fixed_action:
                session["action"] = fixed_action

            session["step"] = "ask_user"
            await callback.message.edit_text("Envie o user_id no privado.")
            await callback.answer()
            return

        if flow == "fwx":
            session["step"] = "fwx_mode"
            await callback.message.edit_text(
                "Escolha a ação:",
                reply_markup=_fwx_mode_keyboard(),
            )
            await callback.answer()
            return

        if flow == "lgx":
            session["step"] = "lgx_notify"
            await callback.message.edit_text(
                "Notificações privadas?",
                reply_markup=_yes_no_keyboard("lgx"),
            )
            await callback.answer()
            return

        if flow == "clx":
            session["step"] = "clx_type"
            await callback.message.edit_text(
                "Escolha o tipo de limpeza:",
                reply_markup=_clean_keyboard(),
            )
            await callback.answer()
            return

        if flow == "fdx":
            session["step"] = "fdx_term"
            await callback.message.edit_text("Envie o termo de busca no privado.")
            await callback.answer()
            return

    if parts[1] == "action":
        if len(parts) < 3:
            await callback.answer()
            return

        action = parts[2]
        session["action"] = action
        await callback.message.edit_reply_markup(reply_markup=_panel_keyboard(action))
        await callback.answer(f"Selecionado: {action}")
        return

    if parts[1] == "duration":
        if len(parts) < 3:
            await callback.answer()
            return

        minutes = int(parts[2])
        session["duration"] = minutes
        await callback.message.edit_reply_markup(reply_markup=_duration_keyboard(minutes))
        await callback.answer(f"Tempo: {minutes}m")
        return

    if parts[1] == "save":
        chat_id = session.get("chat_id")
        user_id = session.get("target_user_id")
        action = session.get("action")

        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            await callback.answer("Dados incompletos.", show_alert=True)
            return

        if not isinstance(action, str):
            await callback.answer("Selecione uma ação.", show_alert=True)
            return

        try:
            if action == "warn":
                _add_warn(chat_id, user_id, "manual_warn")
            else:
                duration = int(session.get("duration") or 10)
                await _execute_action(callback.bot, chat_id, user_id, action, duration)

            await _notify_owner(callback.bot, chat_id, f"Ação executada: {action} | user_id={user_id}")
            await callback.message.edit_text("Sucesso.")
        except TelegramForbiddenError:
            await callback.message.edit_text(
                _error_text(
                    "operação não permitida",
                    "verifique se o bot é administrador do grupo",
                )
            )
        except Exception:
            await callback.message.edit_text(
                _error_text(
                    "falha na execução",
                    "verifique permissões do bot e tente novamente",
                )
            )

        _clear_session(uid)
        await callback.answer()
        return

    if parts[1] == "fwx_mode":
        if len(parts) < 3:
            await callback.answer()
            return

        mode = parts[2]
        if mode not in {"add", "remove"}:
            await callback.answer("Modo inválido.", show_alert=True)
            return

        session["payload"] = {"mode": mode}
        session["step"] = "fwx_words"
        await callback.message.edit_text("Envie palavras separadas por vírgula, ponto e vírgula ou quebra de linha.")
        await callback.answer()
        return

    if parts[1] == "fwx_action":
        if len(parts) < 3:
            await callback.answer()
            return

        action = parts[2]
        if action not in {"vanish", "mute", "warn"}:
            await callback.answer("Ação inválida.", show_alert=True)
            return

        payload = dict(session.get("payload") or {})
        payload["action"] = action
        session["payload"] = payload

        await callback.message.edit_reply_markup(reply_markup=_punishment_keyboard(action))
        await callback.answer(f"Punição: {action}")
        return

    if parts[1] == "fwx_save":
        chat_id = session.get("chat_id")
        payload = dict(session.get("payload") or {})

        if not isinstance(chat_id, int):
            await callback.answer("Grupo inválido.", show_alert=True)
            return

        words = payload.get("words")
        mode = payload.get("mode")
        action = payload.get("action")

        if not isinstance(words, list) or mode not in {"add", "remove"} or action not in {"vanish", "mute", "warn"}:
            await callback.answer("Dados incompletos.", show_alert=True)
            return

        current = _get_rule(chat_id, "words") or {"words": [], "action": action}
        current_words = _normalize_words("\n".join(str(word) for word in current.get("words", [])))
        incoming_words = _normalize_words("\n".join(str(word) for word in words))

        if mode == "add":
            final_words = list(dict.fromkeys(current_words + incoming_words))
        else:
            remove_set = set(incoming_words)
            final_words = [word for word in current_words if word not in remove_set]

        _save_rule(
            chat_id,
            "words",
            {
                "words": final_words,
                "action": action,
            },
        )

        await callback.message.edit_text("Regra de palavras salva.")
        _clear_session(uid)
        await callback.answer()
        return

    if parts[1] == "lgx":
        if len(parts) < 3:
            await callback.answer()
            return

        enabled = parts[2] == "1"
        chat_id = session.get("chat_id")

        if not isinstance(chat_id, int):
            await callback.answer("Grupo inválido.", show_alert=True)
            return

        session["payload"] = {"enabled": enabled}
        await callback.message.edit_reply_markup(reply_markup=_yes_no_keyboard("lgx", enabled))
        await callback.answer("Selecionado")
        return

    if parts[1] == "lgx_save":
        chat_id = session.get("chat_id")
        payload = dict(session.get("payload") or {})

        if not isinstance(chat_id, int):
            await callback.answer("Grupo inválido.", show_alert=True)
            return

        enabled = bool(payload.get("enabled"))
        _save_rule(chat_id, "notify", {"enabled": enabled})

        await callback.message.edit_text(f"Notificações privadas: {'ativas' if enabled else 'inativas'}.")
        _clear_session(uid)
        await callback.answer()
        return

    if parts[1] == "clx_type":
        if len(parts) < 3:
            await callback.answer()
            return

        clean_type = parts[2]
        if clean_type not in {"muted", "banned", "deleted"}:
            await callback.answer("Tipo inválido.", show_alert=True)
            return

        session["payload"] = {"clean_type": clean_type}
        await callback.message.edit_reply_markup(reply_markup=_clean_keyboard(clean_type))
        await callback.answer("Selecionado")
        return

    if parts[1] == "clx_save":
        await callback.message.edit_text("Rotina segura executada.")
        _clear_session(uid)
        await callback.answer()
        return


@router.message(F.from_user.id == OWNER_ID, F.chat.type == "private", F.text.regexp(r"^\d+$"))
async def handle_numeric_input(message: Message) -> None:
    session = _require_session(message.from_user.id)
    if not session:
        return

    step = session.get("step")
    if step != "ask_user":
        return

    user_id = int(message.text or "0")
    session["target_user_id"] = user_id

    flow = str(session.get("flow") or "")

    if flow == "joinx":
        _ensure_join_requests_table()

        chat_id = session.get("chat_id")
        if not isinstance(chat_id, int):
            await message.answer(
                _error_text(
                    "grupo inválido",
                    "repita o comando e selecione o grupo novamente",
                )
            )
            _clear_session(message.from_user.id)
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
            _clear_session(message.from_user.id)
            return

        created_at = _parse_created_at(row["created_at"])
        if created_at is None or created_at < cutoff:
            await message.answer(
                _error_text(
                    "solicitação expirada",
                    "peça para o usuário solicitar entrada novamente",
                )
            )
            _clear_session(message.from_user.id)
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
                    "verifique se o bot é administrador do grupo",
                )
            )
            _clear_session(message.from_user.id)
            return
        except Exception:
            await message.answer(
                _error_text(
                    "falha na aprovação",
                    "verifique os dados e tente novamente",
                )
            )
            _clear_session(message.from_user.id)
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
            f"Usuário {user_id} aprovado."
        )
        _clear_session(message.from_user.id)
        return

    if flow == "mx":
        session["step"] = "duration"
        await message.answer("Escolha o tempo:", reply_markup=_duration_keyboard())
        return

    await message.answer("Painel:", reply_markup=_panel_keyboard(str(session.get("action") or None)))


@router.message(F.from_user.id == OWNER_ID, F.chat.type == "private")
async def handle_text_input(message: Message) -> None:
    session = _require_session(message.from_user.id)
    if not session:
        return

    step = session.get("step")
    flow = session.get("flow")
    text_value = (message.text or "").strip()

    if flow == "fwx" and step == "fwx_words":
        words = _normalize_words(text_value)
        if not words:
            await message.answer(
                _error_text(
                    "nenhuma palavra válida",
                    "envie palavras separadas por vírgula, ponto e vírgula ou quebra de linha",
                )
            )
            return

        payload = dict(session.get("payload") or {})
        payload["words"] = words
        session["payload"] = payload
        session["step"] = "fwx_action"

        await message.answer("Escolha a punição:", reply_markup=_punishment_keyboard())
        return

    if flow == "fdx" and step == "fdx_term":
        chat_id = session.get("chat_id")
        if not isinstance(chat_id, int):
            await message.answer(
                _error_text(
                    "grupo inválido",
                    "repita o comando e selecione o grupo novamente",
                )
            )
            _clear_session(message.from_user.id)
            return

        _ensure_warns_table()
        term = f"%{text_value.lower()}%"

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
        else:
            await message.answer(
                "\n".join(
                    f"{row['user_id']} | {row['reason']} | {row['created_at']}"
                    for row in rows
                )
            )

        _clear_session(message.from_user.id)
        return
