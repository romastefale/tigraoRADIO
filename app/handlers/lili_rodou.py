from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import ChatPermissions

router = Router()


def full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


def no_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_invite_users=False,
    )


def parse_payload(text: str | None) -> tuple[int | None, int | None]:
    if not text:
        return None, None

    try:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        chat_id = int(lines[1])
        user_id = int(lines[2])
        return chat_id, user_id
    except Exception:
        return None, None


async def do_free(bot, chat_id: int, user_id: int) -> None:
    await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=full_permissions(),
    )


async def do_unfree(bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=no_permissions(),
    )


@router.message(Command("lili"))
async def lili(message: types.Message) -> None:
    if message.chat.type != "private":
        return

    chat_id, user_id = parse_payload(message.text)
    if not chat_id or not user_id:
        return

    try:
        await do_free(message.bot, chat_id, user_id)
    except Exception:
        pass


@router.message(Command("rodou"))
async def rodou(message: types.Message) -> None:
    if message.chat.type != "private":
        return

    chat_id, user_id = parse_payload(message.text)
    if not chat_id or not user_id:
        return

    try:
        await do_unfree(message.bot, chat_id, user_id)
    except Exception:
        pass
