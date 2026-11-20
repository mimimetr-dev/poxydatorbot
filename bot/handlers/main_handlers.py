import base64
import contextlib
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
from bot.services import nutrition as nutrition_service
from bot.services.time_utils import calculate_offset_from_user_time, find_timezone_by_offset, get_user_now
from bot.services.typing import typing_indicator

logger = logging.getLogger(__name__)
router = Router()

HELP_TEXT = (
    "🍏 **Как со мной работать**\n\n"
    "**Просто присылай еду — любым удобным способом:**\n"
    "📸 фото блюда — распознаю состав и посчитаю КБЖУ\n"
    "🎙 голосовое — пойму на слух\n"
    "✍ текст — например «омлет из 3 яиц и тост»\n\n"
    "Если данных мало или продукт брендовый — сам уточню через Google-поиск.\n"
    "Любой вопрос словами тоже можно задать: я отвечу, а не запишу в дневник.\n\n"
    "**Команды:**\n"
    "/status — баланс калорий за сегодня\n"
    "/plan — мой план, норма и прогресс\n"
    "/summary — итог дня и саммари\n"
    "/measure — внести вес и талию (раз в 30 дней)\n"
    "/tokens — сколько токенов израсходовано\n"
    "/reset — пройти знакомство заново\n"
    "/help — эта справка\n\n"
    "Работаем только на дефиците калорий: без спортзала и ежедневных взвешиваний 🙂"
)

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
                "Присылай фото еды, голосовое или текст — я всё посчитаю.\n\n" + HELP_TEXT
            )
            return

    await state.set_state(OnboardingStates.waiting_for_ready)
    await message.answer(
        "Привет! Я твой персональный наставник по похудению 👋\n\n"
        "Работаем просто и без стресса: **только дефицит калорий** — без изнурительных тренировок "
        "и без ежедневных взвешиваний. Ты присылаешь фото еды, голосовое или текст, а я считаю калории "
        "и слежу, чтобы дефицит соблюдался.\n\n"
        "**Для старта мне нужно подготовить твои данные.** Это займёт 1–2 минуты:\n"
        "• часовой пояс (определю сам по текущему времени)\n"
        "• пол, возраст, рост\n"
        "• текущий вес и обхват талии\n"
        "• целевой вес\n\n"
        "На основе этого я посчитаю твою личную норму калорий.\n\n"
        "Когда будешь готов — напиши **«готов»** 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Готов ✅", callback_data="onboard_ready")]
        ])
    )


async def _ask_time(message: Message, state: FSMContext):
    await state.set_state(OnboardingStates.waiting_for_time)
    await message.answer(
        "Отлично, поехали! 🚀\n\n"
        "**Шаг 1 из 9. Сколько у тебя сейчас времени?**\n"
        "Напиши, например, `14:35` — по разнице с серверным временем я определю твой часовой пояс "
        "и буду присылать вечерние итоги именно по твоим часам."
    )


@router.callback_query(OnboardingStates.waiting_for_ready, F.data == "onboard_ready")
async def process_ready_callback(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_time(callback.message, state)


@router.message(OnboardingStates.waiting_for_ready)
async def process_ready_text(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if text in ("готов", "готова", "готовы", "да", "поехали", "старт", "start", "го", "ок", "окей"):
        await _ask_time(message, state)
        return

    await message.answer(
        "Как будешь готов — просто напиши **«готов»**, и я начну задавать вопросы 🙂",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Готов ✅", callback_data="onboard_ready")]
        ])
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
        "**Шаг 2 из 9.** Укажи твой пол:",
        reply_markup=kb
    )

@router.callback_query(OnboardingStates.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await callback.message.edit_reply_markup(reply_markup=None)
    
    await state.set_state(OnboardingStates.waiting_for_age)
    await callback.message.answer("**Шаг 3 из 9.** Сколько тебе полных лет?")

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
    await message.answer("**Шаг 4 из 9.** Твой рост в сантиметрах (например, `178`):")

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
    await message.answer("**Шаг 5 из 9.** Текущий вес в килограммах (например, `84.5`):")

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
    await message.answer("**Шаг 6 из 9.** Обхват талии в сантиметрах (в самом узком месте или на уровне пупка, например `92`):")

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
    await message.answer("**Шаг 7 из 9.** Какой твой целевой желаемый вес в кг?")

@router.message(OnboardingStates.waiting_for_target)
async def process_target(message: Message, state: FSMContext):
    try:
        target_weight = float(message.text.replace(",", ".").strip())
        if not (35 <= target_weight <= 300):
            raise ValueError()
    except ValueError:
        await message.answer("Введи целевой вес числом (например, `72`):")
        return

    await state.update_data(target_weight=target_weight)
    await state.set_state(OnboardingStates.waiting_for_activity)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1.2 — сидячий образ жизни", callback_data="act_1.2")],
        [InlineKeyboardButton(text="1.375 — лёгкая активность", callback_data="act_1.375")],
        [InlineKeyboardButton(text="1.55 — средняя активность", callback_data="act_1.55")],
        [InlineKeyboardButton(text="1.725 — высокая активность", callback_data="act_1.725")],
        [InlineKeyboardButton(text="1.9 — очень высокая", callback_data="act_1.9")],
    ])
    await message.answer(
        "**Шаг 8 из 9. Твой уровень повседневной активности**\n\n"
        "Это не про спортзал, а про то, сколько ты двигаешься в обычной жизни — "
        "этот коэффициент умножается на базовый метаболизм.\n\n"
        "• **1.2** — офис/дом, минимум движения\n"
        "• **1.375** — лёгкая активность: много ходьбы, 1–3 тренировки в неделю\n"
        "• **1.55** — средняя: 3–5 тренировок в неделю\n"
        "• **1.725** — высокая: 6–7 тренировок\n"
        "• **1.9** — очень высокая: физический труд + тренировки\n\n"
        "Выбери подходящий вариант:",
        reply_markup=kb
    )


@router.callback_query(OnboardingStates.waiting_for_activity, F.data.startswith("act_"))
async def process_activity(callback: CallbackQuery, state: FSMContext):
    try:
        activity = float(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Не понял коэффициент, выбери ещё раз")
        return

    await callback.answer()
    await state.update_data(activity=activity)
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)

    await state.set_state(OnboardingStates.waiting_for_deficit)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="15% — мягкий и комфортный", callback_data="def_15")],
        [InlineKeyboardButton(text="20% — стандартный", callback_data="def_20")],
    ])
    await callback.message.answer(
        "**Шаг 9 из 9. Размер дефицита калорий**\n\n"
        "Безопасный диапазон — **15–20%** от суточного расхода:\n"
        "• **15%** — медленнее, но легче переносится, меньше срывов\n"
        "• **20%** — быстрее результат, но требует дисциплины\n\n"
        "Выбери:",
        reply_markup=kb
    )


@router.callback_query(OnboardingStates.waiting_for_deficit, F.data.startswith("def_"))
async def process_deficit(callback: CallbackQuery, state: FSMContext):
    try:
        deficit_percent = int(callback.data.split("_")[1])
    except (IndexError, ValueError):
        await callback.answer("Не понял, выбери ещё раз")
        return

    await callback.answer()
    await state.update_data(deficit=deficit_percent)
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await finish_onboarding(callback.message, state)


async def finish_onboarding(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.chat.id

    weight = data["weight"]
    height = data["height"]
    age = data["age"]
    activity = data.get("activity", 1.2)
    deficit_percent = data.get("deficit", 20)
    target_weight = data.get("target_weight")

    # --- Mifflin-St Jeor BMR ---
    # Male:   10*weight + 6.25*height - 5*age + 5
    # Female: 10*weight + 6.25*height - 5*age - 161
    is_male = (data["gender"] == "male")
    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if is_male else -161)

    # --- TDEE = BMR * activity coefficient ---
    tdee = bmr * activity

    # --- Safe deficit 15-20% ---
    deficit_percent = min(20, max(15, deficit_percent))
    raw_target = tdee * (1 - deficit_percent / 100.0)

    # Safety floor (never go below healthy minimum)
    floor = 1500 if is_male else 1200
    floor_applied = raw_target < floor
    target_calories = int(round(max(raw_target, floor) / 10.0) * 10)

    protein_target = int(round(weight * 1.6))

    today = date.today()
    next_checkin = today + timedelta(days=30)

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        user.timezone = data["timezone"]
        user.utc_offset_hours = data["offset"]
        user.gender = data["gender"]
        user.age = age
        user.height_cm = height
        user.target_weight_kg = target_weight
        user.activity_coefficient = activity
        user.deficit_percent = deficit_percent
        user.daily_calories_target = target_calories
        user.daily_protein_target = protein_target
        user.is_onboarded = True
        user.next_checkin_date = next_checkin

        measurement = Measurement(
            telegram_id=user_id,
            date=today,
            weight_kg=weight,
            waist_cm=data["waist"],
            note="Стартовые замеры"
        )
        session.add(measurement)
        await session.commit()

    await state.clear()

    floor_note = ""
    if floor_applied:
        floor_note = (
            f"\n⚠️ Расчётный дефицит ниже безопасного минимума, поэтому норма поднята "
            f"до {floor} ккал — ниже опускаться не стоит.\n"
        )

    await message.answer(
        f"🎯 **Твой персональный план готов!**\n\n"
        f"**Расчёт по формуле Миффлина — Сан Жеора:**\n"
        f"• Базовый метаболизм (BMR): **{int(bmr)} ккал**\n"
        f"• Коэффициент активности: **{activity:g}**\n"
        f"• Суточный расход (TDEE): **{int(tdee)} ккал**\n"
        f"• Дефицит: **{deficit_percent}%** ({int(tdee - target_calories)} ккал)\n"
        f"• **Дневная норма: {target_calories} ккал**\n"
        f"• Белок для сытости: ~{protein_target} г\n"
        f"{floor_note}\n"
        f"🎯 Цель: с {weight:g} кг до {target_weight:g} кг\n\n"
        f"📅 **Следующий замер веса и талии:** {next_checkin.strftime('%d.%m.%Y')} (через 30 дней).\n\n"
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

@router.message(Command("plan"))
async def cmd_plan(message: Message):
    user_id = message.from_user.id
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user or not user.is_onboarded:
            await message.answer("Сначала пройди знакомство — нажми /start 🙂")
            return

        last = await session.execute(
            select(Measurement)
            .where(Measurement.telegram_id == user_id)
            .order_by(Measurement.date.desc())
            .limit(1)
        )
        m = last.scalar_one_or_none()
        weight = m.weight_kg if m else None

        if weight and user.height_cm and user.age:
            is_male = (user.gender == "male")
            bmr = 10 * weight + 6.25 * user.height_cm - 5 * user.age + (5 if is_male else -161)
            tdee = bmr * (user.activity_coefficient or 1.2)
        else:
            bmr = tdee = None

        lines = ["🎯 **Твой план**\n"]
        if weight:
            left = weight - (user.target_weight_kg or weight)
            lines.append(f"• Текущий вес: **{weight:g} кг**")
            lines.append(f"• Цель: **{user.target_weight_kg:g} кг** (осталось {left:.1f} кг)")
        lines.append(f"• Норма: **{user.daily_calories_target} ккал/день**")
        lines.append(f"• Белок: ~{user.daily_protein_target} г")
        if bmr:
            lines.append("")
            lines.append(f"• BMR (Миффлин — Сан Жеор): {int(bmr)} ккал")
            lines.append(f"• Коэффициент активности: {user.activity_coefficient:g}")
            lines.append(f"• TDEE (расход): {int(tdee)} ккал")
            lines.append(f"• Дефицит: {user.deficit_percent}% ({int(tdee - user.daily_calories_target)} ккал)")
        if user.next_checkin_date:
            lines.append("")
            lines.append(f"📅 Следующий замер: **{user.next_checkin_date.strftime('%d.%m.%Y')}**")

        # Today's balance
        today = get_user_now(user.timezone).date()
        eaten = await get_today_calories(session, user_id, today)
        lines.append("")
        lines.append(f"Сегодня: {eaten} / {user.daily_calories_target} ккал (осталось {user.daily_calories_target - eaten})")

    await message.answer("\n".join(lines))


@router.message(Command("summary"))
async def cmd_summary(message: Message):
    from bot.services.scheduler import generate_and_send_summary
    await message.answer("Собираю итоги дня…")
    await generate_and_send_summary(message.bot, message.from_user.id, force=True)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT)


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext):    await message.answer(
        "Сбросить профиль и пройти знакомство заново?\n\n"
        "_История приёмов пищи, замеров и токенов сохранится._",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, сбросить", callback_data="reset_confirm")],
            [InlineKeyboardButton(text="Отмена", callback_data="reset_cancel")],
        ])
    )


@router.callback_query(F.data == "reset_confirm")
async def process_reset_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    async with AsyncSessionLocal() as session:
        user = await session.get(User, callback.from_user.id)
        if user:
            user.is_onboarded = False
            user.next_checkin_date = None
            await session.commit()

    await state.clear()
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Профиль сброшен. Напиши /start, чтобы пройти знакомство заново.")


@router.callback_query(F.data == "reset_cancel")
async def process_reset_cancel(callback: CallbackQuery):
    await callback.answer("Отменено")
    with contextlib.suppress(Exception):
        await callback.message.edit_reply_markup(reply_markup=None)


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

def format_meal_card(parsed: dict, cals_today: int, target: int, icon: str, prefix: str = "Записал") -> str:
    rem = target - cals_today
    search_note = "\n🌐 _Данные уточнены через Google-поиск._" if parsed.get("used_search") else ""
    return (
        f"{icon} {prefix}: **{parsed['dish_name']}**\n"
        f"• Калории: **+{parsed['calories']} ккал**\n"
        f"• БЖУ: Б: {parsed['protein']}г | Ж: {parsed['fat']}г | У: {parsed['carbs']}г\n\n"
        f"💬 _{parsed['comment']}_"
        f"{search_note}\n\n"
        f"📊 На сегодня: **{cals_today} / {target} ккал** (осталось {rem} ккал)"
    )


async def process_food(
    message: Message,
    parts: list,
    input_type: str,
    raw_query: str,
    action_type: str,
    icon: str,
    prefix: str,
    status_msg: Message | None = None
):
    """Common pipeline: analyze -> log tokens -> save meal or answer a question."""
    user_id = message.from_user.id
    try:
        async with typing_indicator(message.bot, message.chat.id):
            parsed, usages = await nutrition_service.analyze(parts, action_type=action_type)

        async with AsyncSessionLocal() as session:
            for u in usages:
                await log_token_usage(session, user_id, u["model"], u["tokens"], u["action"])

            if not parsed["is_food"]:
                answer = parsed.get("answer") or parsed.get("comment") or "Уточни, пожалуйста, вопрос."
                if status_msg:
                    await status_msg.edit_text(answer)
                else:
                    await message.answer(answer)
                return

            user = await session.get(User, user_id)
            if not user:
                return
            await save_meal(session, user_id, input_type, raw_query, parsed)
            user_now = get_user_now(user.timezone)
            cals_today = await get_today_calories(session, user_id, user_now.date())
            card = format_meal_card(parsed, cals_today, user.daily_calories_target, icon, prefix)

        if status_msg:
            await status_msg.edit_text(card)
        else:
            await message.answer(card)

    except Exception as e:
        logger.error(f"Error handling {action_type}: {e}")
        text = "Не получилось обработать запись. Попробуй сформулировать иначе."
        if status_msg:
            await status_msg.edit_text(text)
        else:
            await message.answer(text)


@router.message(F.photo)
async def handle_food_photo(message: Message):
    status_msg = await message.answer("🔍 Анализирую фото еды...")
    photo = message.photo[-1]

    file_info = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    b64_img = base64.b64encode(file_bytes.read()).decode("utf-8")

    parts = [
        {"text": message.caption or "Определи блюдо на фото и рассчитай калории и БЖУ."},
        {"inlineData": {"mimeType": "image/jpeg", "data": b64_img}}
    ]

    await process_food(
        message, parts,
        input_type="photo",
        raw_query=message.caption or "[Фото]",
        action_type="meal_photo",
        icon="🍽",
        prefix="На фото",
        status_msg=status_msg
    )


@router.message(F.voice)
async def handle_food_voice(message: Message):
    status_msg = await message.answer("🎙 Слушаю голосовое...")
    voice = message.voice

    file_info = await message.bot.get_file(voice.file_id)
    file_bytes = await message.bot.download_file(file_info.file_path)
    b64_audio = base64.b64encode(file_bytes.read()).decode("utf-8")

    parts = [
        {"text": "Распознай аудиосообщение о съеденной еде и посчитай калории и БЖУ."},
        {"inlineData": {"mimeType": "audio/ogg", "data": b64_audio}}
    ]

    await process_food(
        message, parts,
        input_type="voice",
        raw_query="[Голосовое сообщение]",
        action_type="meal_voice",
        icon="🎙",
        prefix="Услышал",
        status_msg=status_msg
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_food_text(message: Message):
    parts = [{"text": message.text}]
    await process_food(
        message, parts,
        input_type="text",
        raw_query=message.text,
        action_type="meal_text",
        icon="✍",
        prefix="Записал"
    )

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
