import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean, Text, DateTime, Date, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String(128), nullable=True)
    first_name = Column(String(128), nullable=True)

    timezone = Column(String(64), default="Europe/Moscow", nullable=False)
    utc_offset_hours = Column(Float, default=3.0, nullable=False)

    gender = Column(String(16), nullable=True) # "male", "female"
    age = Column(Integer, nullable=True)
    height_cm = Column(Float, nullable=True)
    target_weight_kg = Column(Float, nullable=True)

    daily_calories_target = Column(Integer, default=1800, nullable=False)
    daily_protein_target = Column(Integer, default=120, nullable=False)

    is_onboarded = Column(Boolean, default=False, nullable=False)
    has_active_subscription = Column(Boolean, default=True, nullable=False)

    registered_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    next_checkin_date = Column(Date, nullable=True)

    measurements = relationship("Measurement", back_populates="user", cascade="all, delete-orphan")
    meals = relationship("Meal", back_populates="user", cascade="all, delete-orphan")
    summaries = relationship("DailySummary", back_populates="user", cascade="all, delete-orphan")
    token_usages = relationship("TokenUsage", back_populates="user", cascade="all, delete-orphan")


class Measurement(Base):
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)
    
    date = Column(Date, nullable=False, index=True)
    weight_kg = Column(Float, nullable=False)
    waist_cm = Column(Float, nullable=False)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="measurements")


class Meal(Base):
    __tablename__ = "meals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)

    local_datetime = Column(DateTime, nullable=False, index=True)
    local_date = Column(Date, nullable=False, index=True)

    input_type = Column(String(32), nullable=False) # "text", "voice", "photo"
    user_query = Column(Text, nullable=True)
    recognized_dish = Column(Text, nullable=False)

    calories = Column(Integer, nullable=False, default=0)
    protein = Column(Float, default=0.0)
    fat = Column(Float, default=0.0)
    carbs = Column(Float, default=0.0)

    ai_analysis = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="meals")


class DailySummary(Base):
    __tablename__ = "daily_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)

    date = Column(Date, nullable=False, index=True)
    total_calories = Column(Integer, default=0, nullable=False)
    target_calories = Column(Integer, default=0, nullable=False)
    deficit_achieved = Column(Boolean, default=False, nullable=False)
    
    summary_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="summaries")


class DialogueHistory(Base):
    __tablename__ = "dialogue_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)
    
    role = Column(String(16), nullable=False) # "user", "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=True, index=True)

    model = Column(String(64), nullable=False)
    prompt_tokens = Column(Integer, default=0, nullable=False)
    completion_tokens = Column(Integer, default=0, nullable=False)
    total_tokens = Column(Integer, default=0, nullable=False)
    
    action_type = Column(String(64), nullable=False) # e.g. "meal_photo", "meal_voice", "meal_text", "summary", "chat"
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="token_usages")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=False, index=True)

    payment_method = Column(String(32), nullable=False) # "telegram_stars", "card"
    amount = Column(Integer, nullable=False)
    currency = Column(String(16), nullable=False) # "XTR", "RUB", etc.
    status = Column(String(32), default="pending", nullable=False) # "pending", "success", "failed"
    provider_payment_charge_id = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
