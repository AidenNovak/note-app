from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ping_db():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


def get_sync_database_url() -> str:
    if settings.DATABASE_URL.startswith("sqlite+aiosqlite:"):
        return settings.DATABASE_URL.replace("sqlite+aiosqlite:", "sqlite:", 1)
    if settings.DATABASE_URL.startswith("postgresql+asyncpg://"):
        return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return settings.DATABASE_URL
