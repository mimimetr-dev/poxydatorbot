import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from bot.config import settings
from bot.database.db import AsyncSessionLocal
from bot.database.models import User, Payment

logger = logging.getLogger(__name__)
router = Router()

@router.message(Command("subscribe"))
@router.message(Command("pay"))
async def cmd_pay(message: Message):
    buttons = []
    
    if settings.ENABLE_TELEGRAM_STARS:
        buttons.append([InlineKeyboardButton(text="⭐ Оплатить 250 Stars", callback_data="pay_stars_month")])
        
    if settings.ENABLE_CARD_PAYMENTS:
        buttons.append([InlineKeyboardButton(text="💳 Оплатить картой 490 ₽", callback_data="pay_card_month")])

    if not buttons:
        await message.answer("В данный момент все функции бота полностью бесплатны для вас!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        "✨ **Подписка на персонального нутрициолога**\n\n"
        "• Неограниченное распознавание еды по фото и голосовым\n"
        "• Ежедневные саммари и контроль дефицита калорий\n"
        "• Ежемесячный анализ прогресса\n\n"
        "Выберите удобный способ оплаты:",
        reply_markup=kb
    )

# Telegram Stars Payment
@router.callback_query(F.data == "pay_stars_month")
async def process_pay_stars(callback):
    if not settings.ENABLE_TELEGRAM_STARS:
        await callback.answer("Оплата Stars временно отключена", show_alert=True)
        return

    prices = [LabeledPrice(label="Подписка на 1 месяц (Stars)", amount=250)]
    await callback.message.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Подписка на 1 месяц",
        description="Полный доступ к персональному AI-нутрициологу",
        payload="sub_1_month_stars",
        currency="XTR",
        prices=prices
    )
    await callback.answer()

# Card Payment (via YooKassa/Stripe provider token)
@router.callback_query(F.data == "pay_card_month")
async def process_pay_card(callback):
    if not settings.ENABLE_CARD_PAYMENTS or not settings.PAYMENT_PROVIDER_TOKEN:
        await callback.answer("Оплата картой временно отключена", show_alert=True)
        return

    prices = [LabeledPrice(label="Подписка на 1 месяц", amount=49000)] # 490.00 RUB
    await callback.message.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Подписка на 1 месяц (Карта)",
        description="Полный доступ к персональному AI-нутрициологу",
        payload="sub_1_month_card",
        provider_token=settings.PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices
    )
    await callback.answer()

@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    sp = message.successful_payment
    user_id = message.from_user.id
    
    method = "telegram_stars" if sp.currency == "XTR" else "card"
    
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if user:
            user.has_active_subscription = True
            
        p = Payment(
            telegram_id=user_id,
            payment_method=method,
            amount=sp.total_amount,
            currency=sp.currency,
            status="success",
            provider_payment_charge_id=sp.provider_payment_charge_id
        )
        session.add(p)
        await session.commit()

    await message.answer("🎉 Оплата успешно прошла! Спасибо за поддержку. Подписка активна.")
