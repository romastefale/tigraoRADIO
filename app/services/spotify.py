from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from app.config.settings import TELEGRAM_BOT_TOKEN


logger = logging.getLogger(__name__)

bot: Bot | None = None
dp: Dispatcher | None = None


async def startup_telegram_bot() -> None:
    global bot, dp

    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not set. Skipping bot startup.")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # ========================
    # COMMANDS
    # ========================

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer(
            "🤖 Bot ativo!\n\nUse /play para ver a música atual."
        )

    @dp.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        await message.answer(
            "Comandos disponíveis:\n"
            "/start - iniciar\n"
            "/play - ver música\n"
            "/help - ajuda"
        )

    @dp.message(Command("play"))
    async def play(message: Message) -> None:
        await message.answer(
            "🎧 Spotify ainda está sendo configurado..."
        )

    # ========================
    # START POLLING
    # ========================

    logger.info("Starting Telegram bot polling...")
    await dp.start_polling(bot)


async def shutdown_telegram_bot() -> None:
    global bot

    if bot:
        await bot.session.close()
        logger.info("Telegram bot stopped.")