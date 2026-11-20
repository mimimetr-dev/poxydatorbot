import base64
import json
import logging
from datetime import date, datetime, timedelta
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func

from bot.config import settings
from bot.database.db import AsyncSessionLocal
from bot.database.models import User, Measurement, Meal, DailySummary, TokenUsage
from bot.handlers.states import OnboardingStates, MeasurementStates
from bot.services.llm import llm_service
from bot.services.time_utils import calculate_offset_from_user_time, find_timezone_by_offset, get_user_now

logger = logging.getLogger(__name__)
router = Router()

# Helper to log tokens
async def log_token_usage(session, user_id: int, model: str, tokens: dict, action: str):
    tu = TokenUsage(
        telegram_id=user_id,
        model=model,
        prompt_tokens=tokens.get("prompt", 0),
        completion_tokens=tokens.get("completion", 0),
        total_tokens=tokens.get("total", 0),
        action_type=action
    )
    session.add(tu)
    await session.commit()

# --- START & ONBOARDING ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            user = User(
                telegram_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name
            )
            session.add(user)
            await session.commit()
        
        if user.is_onboarded:
            await message.answer(
                f"Рад снова видеть тебя, {user.first_name or ''}!\n\n"
                f"Твой дневной лимит: **{user.daily_calories_target} ккал**.\n"
                "Присылай фото еды, голосовое или текст — я всё посчитаю.\n\n"
                "Команды:\n"
                "/status — баланс калорий за сегодня\n"
                "/tokens — статистика потраченных токенов\n"
                "/summary — саммари сегодняшнего дня\n"
                "/measure — внести контрольные замеры"
            )
            return

    await state.set_state(OnboardingStates.waiting_for_time)
    await message.answer(
        "Привет! Я твой персональный наставник по похудению.\n\n"
        "Мы работаем просто и без стресса: **только дефицит калорий**, без изнурительных тренировок и без ежедневных взвешиваний.\n\n"
        "Давай настроимся под тебя. **Сколько у тебя сейчас времени?**\n"
        "(Напиши например `14:35` или `9:10`, я автоматически определю часовой пояс)"
    )

@router.message(OnboardingStates.waiting_for_time)
async def process_time(message: Message, state: FSMContext):
    offset = calculate_offset_from_user_time(message.text)
    if offset is None:
        await message.answer("Пожалуйста, напиши текущее время в формате `ЧЧ:ММ` (например, `15:30`):")
        return
    
    tz_name = find_timezone_by_offset(offset)
    await state.update_data(offset=offset, timezone=tz_name)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Мужской 👨", callback_data="gender_male"),
            InlineKeyboardButton(text="Женский 👩", callback_data="gender_female")
        ]
    ])
    await state.set_state(OnboardingStates.waiting_for_gender)
    await message.answer(
        f"Супер! Определил твой часовой пояс: **{tz_name}** (UTC{'+' if offset>=0 else ''}{offset:g}ч).\n\n"
        "Укажи твой пол:",
        reply_markup=kb
    )

@router.callback_query(OnboardingStates.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await callback.message.edit_reply_markup(reply_markup=None)
    
    await state.set_state(OnboardingStates.waiting_for_age)
    await callback.message.answer("Сколько тебе полных лет?")

@router.message(OnboardingStates.waiting_for_age)
async def process_age(message: Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        if not (14 <= age <= 100):
            raise ValueError()
    except ValueError:
        await message.answer("Пожалуйста, введи корректный возраст числом (от 14 до 100):")
        return

    await state.update_data(age=age)
    await state.set_state(OnboardingStates.waiting_for_height)
    await message.answer("Твой рост в сантиметрах (например, `178`):")

@router.message(OnboardingStates.waiting_for_height)
async def process_height(message: Message, state: FSMContext):
    try:
        height = float(message.text.replace(",", ".").strip())
        if not (120 <= height <= 250):
            raise ValueError()
    except ValueError:
        await message.answer("Введи корректный рост числом в сантиметрах (например, `175`):")
        return

    await state.update_data(height=height)
    await state.set_state(OnboardingStates.waiting_for_weight)
    await message.answer("Текущий вес в килограммах (например, `84.5`):")

@router.message(OnboardingStates.waiting_for_weight)
async def process_weight(message: Message, state: FSMContext):
    try:
        weight = float(message.text.replace(",", ".").strip())
        if not (35 <= weight <= 300):
            raise ValueError()
    except ValueError:
        await message.answer("Введи корректный вес в кг (например, `80` или `82.5`):")
        return

    await state.update_data(weight=weight)
    await state.set_state(OnboardingStates.waiting_for_waist)
    await message.answer("Обхват талии в сантиметрах (в самом узком месте или на уровне пупка, например `92`):")

@router.message(OnboardingStates.waiting_for_waist)
async def process_waist(message: Message, state: FSMContext):
    try:
        waist = float(message.text.replace(",", ".").strip())
        if not (45 <= waist <= 250):
            raise ValueError()
    except ValueError:
        await message.answer("Введи корректный обхват талии в см (например, `88`):")
        return

    await state.update_data(waist=waist)
    await state.set_state(OnboardingStates.waiting_for_target)
    await message.answer("Какой твой целевой желаемый вес в кг?")

@router.message(OnboardingStates.waiting_for_target)
async def process_target(message: Message, state: FSMContext):
    try:
        target_weight = float(message.text.replace(",", ".").strip())
        if not (35 <= target_weight <= 300):
            raise ValueError()
    except ValueError:
        await message.answer("Введи целевой вес числом (например, `72`):")
        return

    data = await state.get_data()
    user_id = message.from_user.id
    
    # Calculate Mifflin-St Jeor TDEE with sedentary activity (1.2) - comfortable deficit (20%)
    # BMR:
    # Male: 10 * weight + 6.25 * height - 5 * age + 5
    # Female: 10 * weight + 6.25 * height - 5 * age - 161
    is_male = (data["gender"] == "male")
    bmr = 10 * data["weight"] + 6.25 * data["height"] - 5 * data["age"] + (5 if is_male else -161)
    tdee = bmr * 1.2
    target_calories = int(tdee * 0.8) # 20% deficit
    protein_target = int(data["weight"] * 1.5)

    today = date.today()
    next_checkin = today + timedelta(days=30)

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        user.timezone = data["timezone"]
        user.utc_offset_hours = data["offset"]
        user.gender = data["gender"]
        user.age = data["age"]
        user.height_cm = data["height"]
        user.target_weight_kg = target_weight
        user.daily_calories_target = target_calories
        user.daily_protein_target = protein_target
        user.is_onboarded = True
        user.next_checkin_date = next_checkin

        measurement = Measurement(
            telegram_id=user_id,
            date=today,
            weight_kg=data["weight"],
            waist_cm=data["waist"],
            note="Стартовые замеры"
        )
        session.add(measurement)
        await session.commit()

    await state.clear()
    await message.answer(
        f"🎯 **Твой персональный план готов!**\n\n"
        f"• Базовый расход: ~{int(tdee)} ккал\n"
        f"• **Дневная норма с дефицитом: {target_calories} ккал**\n"
        f"• Рекомендуемый белок: ~{protein_target} г (для чувства сытости)\n"
        f"• Цель: с {data['weight']} кг до {target_weight} кг\n\n"
        f"📅 **Следующий замер веса и талии:** {next_checkin.strftime('%d.%m.%Y')} (ровно через 30 дней).\n\n"
        "Теперь просто присылай мне в любое время:\n"
        "📸 **Фото блюда**\n"
        "🎙 **Голосовое сообщение**\n"
        "✍ **Текст**\n\n"
        "Я всё распознаю, посчитаю калории и поддержу!"
    )

# --- DAILY STATUS & STATS ---

@router.message(Command("status"))
async def cmd_status(message: Message):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return
        
        user_now = get_user_now(user.timezone)
        today = user_now.date()

        res = await session.execute(
            select(
                func.coalesce(func.sum(Meal.calories), 0),
                func.coalesce(func.sum(Meal.protein), 0.0),
                func.coalesce(func.sum(Meal.fat), 0.0),
                func.coalesce(func.sum(Meal.carbs), 0.0)
            ).where(Meal.telegram_id == user_id, Meal.local_date == today)
        )
        cals, prot, fat, carbs = res.one()

        remaining = user.daily_calories_target - cals
        pct = int((cals / user.daily_calories_target) * 100) if user.daily_calories_target else 0

        status_msg = (
            f"📊 **Баланс за сегодня ({today.strftime('%d.%m')}):**\n\n"
            f"• Съедено: **{cals}** / {user.daily_calories_target} ккал ({pct}%)\n"
            f"• Осталось: **{remaining} ккал**\n"
            f"• БЖУ: Б: {prot:.0f}г | Ж: {fat:.0f}г | У: {carbs:.0f}г\n\n"
        )
        if remaining > 0:
            status_msg += " Ты отлично держишь дефицит! Продолжай в том же духе."
        else:
            status_msg += " Лимит превышен, но ничего критичного. Завтра вернемся в норму."

        await message.answer(status_msg)

@router.message(Command("tokens"))
async def cmd_tokens(message: Message):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(
                func.coalesce(func.sum(TokenUsage.prompt_tokens), 0),
                func.coalesce(func.sum(TokenUsage.completion_tokens), 0),
                func.coalesce(func.sum(TokenUsage.total_tokens), 0),
                func.count(TokenUsage.id)
            ).where(TokenUsage.telegram_id == user_id)
        )
        p_tok, c_tok, total, calls = res.one()

        await message.answer(
            f"📈 **Статистика токенов Comet API:**\n\n"
            f"• Всего обращений к моделям: **{calls}**\n"
            f"• Prompt токенов (вход): **{p_tok}**\n"
            f"• Completion токенов (ответ): **{c_tok}**\n"
            f"• **Итого потрачено токенов: {total}**"
        )

# --- MONTHLY MEASUREMENT ---

@router.message(Command("measure"))
async def cmd_measure(message: Message, state: FSMContext):
    await state.set_state(MeasurementStates.waiting_for_monthly_data)
    await message.answer(
        "📏 **Контрольный замер**\n\n"
        "Введи текущий вес и талию через пробел (например: `81.2 90`):"
    )

@router.message(MeasurementStates.waiting_for_monthly_data)
async def process_monthly_measurement(message: Message, state: FSMContext):
    parts = message.text.replace(",", ".").split()
    if len(parts) < 2:
        await message.answer("Пожалуйста, напиши два числа через пробел (вес и талию):")
        return
    try:
        weight = float(parts[0])
        waist = float(parts[1])
    except ValueError:
        await message.answer("Неверный формат чисел. Попробуй ещё раз:")
        return

    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        today = date.today()

        # Find previous measurement
        prev_res = await session.execute(
            select(Measurement)
            .where(Measurement.telegram_id == user_id)
            .order_by(Measurement.date.desc())
            .limit(1)
        )
        prev_m = prev_res.scalar_one_or_none()

        m = Measurement(
            telegram_id=user_id,
            date=today,
            weight_kg=weight,
            waist_cm=waist,
            note="Ежемесячный замер"
        )
        session.add(m)
        user.next_checkin_date = today + timedelta(days=30)
        await session.commit()

        delta_w_str = ""
        delta_waist_str = ""
        if prev_m:
            dw = weight - prev_m.weight_kg
            dt = waist - prev_m.waist_cm
            delta_w_str = f" ({'+' if dw>0 else ''}{dw:.1f} кг)"
            delta_waist_str = f" ({'+' if dt>0 else ''}{dt:.1f} см)"

        await state.clear()
        await message.answer(
            f"🎉 **Замеры сохранены!**\n\n"
            f"• Вес: **{weight} кг**{delta_w_str}\n"
            f"• Талия: **{waist} см**{delta_waist_str}\n\n"
            f"Следующий замер запланирован на: **{user.next_checkin_date.strftime('%d.%m.%Y')}**.\n"
            "Продолжаем держать дефицит калорий!"
        )

# --- MEAL INPUT: PHOTO, VOICE, TEXT ---

NUTRITION_SYSTEM_PROMPT = """Ты строгий и точный персональный нутрициолог.
Твоя цель: оценить калорийность и состав блюда пользователя с максимальной точностью.
Фокус ТОЛЬКО на дефиците калорий.
Ответь СТРОГО в формате валидного JSON (без лишних оберток Markdown):
{
  "dish_name": "Название блюда и порция",
  "calories": 450,
  "protein": 25.0,
  "fat": 15.0,
  "carbs": 50.0,
  "comment": "Краткий ободряющий комментарий (1-2 предложения)"
}
Если по фото или описанию калорийность трудно определить точно, сделай реалистичную среднюю оценку.
"""

@router.message(F.photo)
async def handle_food_photo(message: Message):
    status_msg = await message.answer("🔍 Анализирую фото еды...")
    user_id = message.from_user.id
    photo = message.photo[-1] # Largest photo
    
    file_info = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    b64_img = base64.b64encode(file_bytes.read()).decode("utf-8")

    parts = [
        {"text": message.caption or "Определи блюдо на фото и рассчитай калории и БЖУ."},
        {
            "inlineData": {
                "mimeType": "image/jpeg",
                "data": b64_img
            }
        }
    ]

    try:
        res = await llm_service.generate_response(
            contents_or_parts=parts,
            system_instruction=NUTRITION_SYSTEM_PROMPT,
            action_type="meal_photo",
            enable_web_search=True
        )
        parsed = clean_and_parse_json(res["text"])
        
        async with AsyncSessionLocal() as session:
            await log_token_usage(session, user_id, res["model_used"], res["tokens"], "meal_photo")
            await save_meal(session, user_id, "photo", message.caption, parsed)
            user = await session.get(User, user_id)
            user_now = get_user_now(user.timezone)
            cals_today = await get_today_calories(session, user_id, user_now.date())

        rem = user.daily_calories_target - cals_today
        await status_msg.edit_text(
            f"🍽 **{parsed['dish_name']}**\n"
            f"• Калории: **+{parsed['calories']} ккал**\n"
            f"• БЖУ: Б: {parsed['protein']}г | Ж: {parsed['fat']}г | У: {parsed['carbs']}г\n\n"
            f"💬 _{parsed['comment']}_\n\n"
            f"📊 На сегодня: **{cals_today} / {user.daily_calories_target} ккал** (осталось {rem} ккал)"
        )
    except Exception as e:
        logger.error(f"Error handling photo: {e}")
        await status_msg.edit_text("Не удалось распознать фото. Попробуй описать текстом или прислать другое фото.")

@router.message(F.voice)
async def handle_food_voice(message: Message):
    status_msg = await message.answer("🎙 Слушаю голосовое...")
    user_id = message.from_user.id
    voice = message.voice
    
    file_info = await message.bot.get_file(voice.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    b64_audio = base64.b64encode(file_bytes.read()).decode("utf-8")

    parts = [
        {"text": "Распознай аудиосообщение о съеденной еде, посчитай калории и БЖУ."},
        {
            "inlineData": {
                "mimeType": "audio/ogg",
                "data": b64_audio
            }
        }
    ]

    try:
        res = await llm_service.generate_response(
            contents_or_parts=parts,
            system_instruction=NUTRITION_SYSTEM_PROMPT,
            action_type="meal_voice",
            enable_web_search=True
        )
        parsed = clean_and_parse_json(res["text"])

        async with AsyncSessionLocal() as session:
            await log_token_usage(session, user_id, res["model_used"], res["tokens"], "meal_voice")
            await save_meal(session, user_id, "voice", "[Голосовое сообщение]", parsed)
            user = await session.get(User, user_id)
            user_now = get_user_now(user.timezone)
            cals_today = await get_today_calories(session, user_id, user_now.date())

        rem = user.daily_calories_target - cals_today
        await status_msg.edit_text(
            f"🎙 Распознано: **{parsed['dish_name']}**\n"
            f"• Калории: **+{parsed['calories']} ккал**\n"
            f"• БЖУ: Б: {parsed['protein']}г | Ж: {parsed['fat']}г | У: {parsed['carbs']}г\n\n"
            f"💬 _{parsed['comment']}_\n\n"
            f"📊 На сегодня: **{cals_today} / {user.daily_calories_target} ккал** (осталось {rem} ккал)"
        )
    except Exception as e:
        logger.error(f"Error handling voice: {e}")
        await status_msg.edit_text("Не удалось разобрать голосовое. Пожалуйста, повтори или напиши текстом.")

@router.message(F.text & ~F.text.startswith("/"))
async def handle_food_text(message: Message):
    user_id = message.from_user.id
    text = message.text

    parts = [{"text": f"Пользователь съел: '{text}'. Посчитай калории и БЖУ."}]

    try:
        res = await llm_service.generate_response(
            contents_or_parts=parts,
            system_instruction=NUTRITION_SYSTEM_PROMPT,
            action_type="meal_text",
            enable_web_search=True
        )
        parsed = clean_and_parse_json(res["text"])

        async with AsyncSessionLocal() as session:
            await log_token_usage(session, user_id, res["model_used"], res["tokens"], "meal_text")
            await save_meal(session, user_id, "text", text, parsed)
            user = await session.get(User, user_id)
            user_now = get_user_now(user.timezone)
            cals_today = await get_today_calories(session, user_id, user_now.date())

        rem = user.daily_calories_target - cals_today
        await message.answer(
            f"✍ Записал: **{parsed['dish_name']}**\n"
            f"• Калории: **+{parsed['calories']} ккал**\n"
            f"• БЖУ: Б: {parsed['protein']}г | Ж: {parsed['fat']}г | У: {parsed['carbs']}г\n\n"
            f"💬 _{parsed['comment']}_\n\n"
            f"📊 На сегодня: **{cals_today} / {user.daily_calories_target} ккал** (осталось {rem} ккал)"
        )
    except Exception as e:
        logger.error(f"Error handling text: {e}")
        await message.answer("Не получилось обработать запись. Попробуй уточнить блюдо.")

# --- UTILS ---

def clean_and_parse_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())

async def save_meal(session, user_id: int, input_type: str, raw_query: str, parsed: dict):
    user = await session.get(User, user_id)
    user_now = get_user_now(user.timezone)
    meal = Meal(
        telegram_id=user_id,
        local_datetime=user_now.replace(tzinfo=None),
        local_date=user_now.date(),
        input_type=input_type,
        user_query=raw_query,
        recognized_dish=parsed.get("dish_name", "Блюдо"),
        calories=parsed.get("calories", 0),
        protein=parsed.get("protein", 0.0),
        fat=parsed.get("fat", 0.0),
        carbs=parsed.get("carbs", 0.0),
        ai_analysis=parsed.get("comment", "")
    )
    session.add(meal)
    await session.commit()

async def get_today_calories(session, user_id: int, target_date: date) -> int:
    res = await session.execute(
        select(func.coalesce(func.sum(Meal.calories), 0))
        .where(Meal.telegram_id == user_id, Meal.local_date == target_date)
    )
    return res.scalar_one()
