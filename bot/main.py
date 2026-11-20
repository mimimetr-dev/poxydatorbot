import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from bot.config import settings
from bot.database.db import init_db
from bot.middlewares.whitelist import WhitelistMiddleware
from bot.handlers.main_handlers import router as main_router
from bot.handlers.payment_handlers import router as payment_router
from bot.services.scheduler import setup_scheduler

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

    logger.info(f"Bot started. Allowed user ID: {settings.ALLOWED_USER_ID}")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
