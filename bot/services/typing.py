import asyncio
import contextlib
import logging
from typing import AsyncIterator

from aiogram import Bot
from aiogram.enums import ChatAction

logger = logging.getLogger(__name__)


async def _typing_loop(bot: Bot, chat_id: int, interval: float) -> None:
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"typing indicator failed: {e}")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def typing_indicator(
    bot: Bot,
    chat_id: int,
    interval: float = 4.5
) -> AsyncIterator[None]:
    """
    Keeps the "bot is typing..." status visible while a long operation runs.
    Telegram clears the action after ~5 seconds, so it is refreshed periodically.
    """
    task = asyncio.create_task(_typing_loop(bot, chat_id, interval))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
