import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from bot.config import settings
from bot.database.db import init_db
from bot.middlewares.whitelist import WhitelistMiddleware
from bot.handlers.main_handlers import router as main_router
from bot.handlers.payment_handlers import router as payment_router
from bot.services.scheduler import setup_scheduler

SHORT_DESCRIPTION = (
    "AI-нутрициолог: считает калории по фото, голосу и тексту. "
    "Дефицит калорий без спортзала и ежедневных взвешиваний."
)

DESCRIPTION = (
    "Привет! Я AI-бот для похудения на дефиците калорий.\n\n"
    "📸 Пришли фото еды, 🎙 голосовое или ✍ текст — я распознаю блюдо, "
    "посчитаю калории и БЖУ и запишу в дневник.\n\n"
    "• Личная норма по формуле Миффлина — Сан Жеора\n"
    "• Безопасный дефицит 15–20%\n"
    "• Вечернее саммари каждый день\n"
    "• Замеры веса и талии раз в месяц\n"
    "• Уточняю данные через Google-поиск, если блюдо непонятное\n\n"
    "Нажми /start, чтобы начать 👇"
)

BOT_COMMANDS = [
    BotCommand(command="start", description="Главное меню / знакомство"),
    BotCommand(command="status", description="Баланс калорий за сегодня"),
    BotCommand(command="plan", description="Мой план и норма калорий"),
    BotCommand(command="summary", description="Итог дня и саммари"),
    BotCommand(command="measure", description="Внести вес и талию"),
    BotCommand(command="tokens", description="Расход токенов модели"),
    BotCommand(command="reset", description="Пройти знакомство заново"),
    BotCommand(command="help", description="Что умеет бот"),
]


async def setup_bot_profile(bot: Bot) -> None:
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        logger.info("Bot commands menu updated.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")
    try:
        await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
        await bot.set_my_description(description=DESCRIPTION)
        logger.info("Bot description updated.")
    except Exception as e:
        logger.warning(f"Failed to set bot description: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Initializing database...")
    await init_db()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    dp = Dispatcher()

    # Apply Whitelist middleware
    dp.message.middleware(WhitelistMiddleware())
    dp.callback_query.middleware(WhitelistMiddleware())

    # Include routers
    dp.include_router(main_router)
    dp.include_router(payment_router)

    # Setup background scheduler for evening summaries and monthly checks
    scheduler = setup_scheduler(bot)
    logger.info("Background scheduler started.")

    await setup_bot_profile(bot)

    logger.info(f"Bot started. Allowed user ID: {settings.ALLOWED_USER_ID}")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
