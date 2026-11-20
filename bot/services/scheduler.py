import logging
from datetime import date
from aiogram import Bot
from sqlalchemy import select, func
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.database.db import AsyncSessionLocal
from bot.database.models import User, Meal, DailySummary, TokenUsage
from bot.services.llm import llm_service
from bot.services.time_utils import get_user_now
from bot.services.typing import typing_indicator

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Ты заботливый, но честный персональный наставник по дефициту калорий.
Сделай краткое вечернее саммари дня для пользователя.
Оцени:
1. Итоговые калории относительно нормы.
2. Был ли удержан дефицит.
3. Поддержи пользователя без чувства вины, дай 1 конкретный позитивный совет на завтра.
Длина сообщения: до 4-5 предложений. Будь живым и мотивирующим.
"""

async def generate_and_send_summary(bot: Bot, user_id: int, force: bool = False):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or not user.is_onboarded:
            return

        user_now = get_user_now(user.timezone)
        today = user_now.date()

        # Check if already summarized today
        existing = await session.execute(
            select(DailySummary).where(DailySummary.telegram_id == user_id, DailySummary.date == today)
        )
        existing_row = existing.scalar_one_or_none()
        if existing_row and not force:
            return
        if existing_row and force:
            await session.delete(existing_row)
            await session.commit()

        # Fetch meals for today
        meals_res = await session.execute(
            select(Meal).where(Meal.telegram_id == user_id, Meal.local_date == today).order_by(Meal.local_datetime.asc())
        )
        meals = meals_res.scalars().all()

        total_cals = sum(m.calories for m in meals)
        meals_desc = "\n".join([f"- {m.recognized_dish}: {m.calories} ккал" for m in meals]) or "Записей не было"

        content = [
            {"text": f"Норма калорий пользователя: {user.daily_calories_target} ккал.\n"
                     f"Съедено за день: {total_cals} ккал.\n"
                     f"Список блюд:\n{meals_desc}"}
        ]

        try:
            async with typing_indicator(bot, user_id, interval=4.5):
                res = await llm_service.generate_response(
                    contents_or_parts=content,
                    system_instruction=SUMMARY_PROMPT,
                    action_type="daily_summary"
                )
            summary_text = res["text"]

            # Save summary & tokens
            tu = TokenUsage(
                telegram_id=user_id,
                model=res["model_used"],
                prompt_tokens=res["tokens"]["prompt"],
                completion_tokens=res["tokens"]["completion"],
                total_tokens=res["tokens"]["total"],
                action_type="daily_summary"
            )
            session.add(tu)

            deficit_ok = total_cals <= user.daily_calories_target and total_cals > 0
            ds = DailySummary(
                telegram_id=user_id,
                date=today,
                total_calories=total_cals,
                target_calories=user.daily_calories_target,
                deficit_achieved=deficit_ok,
                summary_text=summary_text
            )
            session.add(ds)
            await session.commit()

            msg = (
                f"🌙 **Вечерний итог дня ({today.strftime('%d.%m')}):**\n\n"
                f"• Итого за день: **{total_cals}** / {user.daily_calories_target} ккал\n"
                f"• Статус: {'✅ Дефицит соблюден!' if deficit_ok else '⚠️ Внимание к калоражу'}\n\n"
                f"{summary_text}"
            )
            await bot.send_message(chat_id=user_id, text=msg)

            # Check monthly measurement reminder
            if user.next_checkin_date and today >= user.next_checkin_date:
                await bot.send_message(
                    chat_id=user_id,
                    text="🔔 **Прошел 1 месяц! День контрольного замера!**\n\n"
                         "Пожалуйста, сделай замер веса и талии и отправь команду /measure."
                )

        except Exception as e:
            logger.error(f"Error generating daily summary for {user_id}: {e}")

async def check_scheduled_summaries(bot: Bot):
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).where(User.is_onboarded == True))).scalars().all()
        for u in users:
            user_now = get_user_now(u.timezone)
            if user_now.hour == 22 and user_now.minute < 10:
                await generate_and_send_summary(bot, u.telegram_id)

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_scheduled_summaries, "interval", minutes=10, args=[bot])
    scheduler.start()
    return scheduler
