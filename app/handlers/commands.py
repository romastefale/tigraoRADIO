from aiogram import Router
from aiogram.types import Message

from app.bot.intent import detect_intent
from app.core.runtime import allow

router = Router()

# ------------------------
# COMANDOS BASE
# ------------------------

@router.message()
async def natural_handler(message: Message):
    if not message.text or not message.from_user:
        return

    user_id = message.from_user.id

    if not allow(user_id):
        return

    intent = detect_intent(message.text)

    if not intent:
        return

    await message.reply(f"Intent detectada: {intent}")
