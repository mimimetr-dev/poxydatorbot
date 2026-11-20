from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    BOT_TOKEN: str = "placeholder"
    ALLOWED_USER_ID: int = 6193695760

    COMETAPI_KEY: str = ""
    COMETAPI_BASE_URL: str = "https://api.cometapi.com"

    PRIMARY_MODEL: str = "gemini-3.8-flash"
    FALLBACK_MODEL: str = "gpt-6-luna"

    DATABASE_URL: str = "sqlite+aiosqlite:///poxydator.db"

    ENABLE_TELEGRAM_STARS: bool = False
    ENABLE_CARD_PAYMENTS: bool = False
    PAYMENT_PROVIDER_TOKEN: Optional[str] = None

    DEFAULT_TIMEZONE: str = "Europe/Moscow"
    EVENING_SUMMARY_HOUR: int = 22
    EVENING_SUMMARY_MINUTE: int = 0

settings = Settings()
