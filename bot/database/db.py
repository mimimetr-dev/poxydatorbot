from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from bot.config import settings
from bot.database.models import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Lightweight self-migration: keep long-lived SQLite data safe when the schema grows.
# (Once the project moves to PostgreSQL + Alembic, this block can be dropped.)
LEGACY_COLUMNS = {
    "users": {
        "activity_coefficient": "FLOAT NOT NULL DEFAULT 1.2",
        "deficit_percent": "INTEGER NOT NULL DEFAULT 20",
    },
}


async def _apply_light_migrations(conn) -> None:
    if engine.dialect.name != "sqlite":
        return

    for table, columns in LEGACY_COLUMNS.items():
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_light_migrations(conn)
