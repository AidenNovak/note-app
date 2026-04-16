from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
_is_asyncpg = settings.DATABASE_URL.startswith("postgresql+asyncpg://")

# Postgres/Supavisor tuning:
# - pool_pre_ping=False  -> avoid an extra RTT on every checkout (Supabase-SG round-trip ~400ms)
# - pool_recycle=1800    -> Supavisor idle timeout is 30min; recycle just before it
# - statement_cache_size=0 when going through Supavisor transaction-pooler (prepared-stmt conflicts)
_pg_connect_args = {}
if _is_asyncpg:
    _pg_connect_args = {
        # Disable server-side prepared-statement cache; safe for both session + transaction mode.
        "statement_cache_size": 0,
        # Re-use a single connection-level prepared cache keyed locally.
        "prepared_statement_cache_size": 0,
    }

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    **({} if _is_sqlite else {
        "pool_size": 10,
        "max_overflow": 10,
        "pool_pre_ping": False,
        "pool_recycle": 1800,
        "pool_timeout": 10,
        "connect_args": _pg_connect_args,
    }),
)
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


async def warm_pool(target: int = 5) -> None:
    """Pre-open `target` real connections at boot so the first few user requests
    don't pay a cold-connect tax to Supavisor (~1.5-2s per new connection).
    Runs N parallel SELECT 1's that each hold a connection briefly."""
    if _is_sqlite:
        return

    async def _one():
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            logger.exception("pool_warm_failed")

    try:
        await asyncio.wait_for(
            asyncio.gather(*[_one() for _ in range(target)]),
            timeout=10.0,
        )
        logger.info("db_pool_warmed", extra={"connections": target})
    except asyncio.TimeoutError:
        logger.warning("db_pool_warm_timeout", extra={"connections": target})


def get_sync_database_url() -> str:
    if settings.DATABASE_URL.startswith("sqlite+aiosqlite:"):
        return settings.DATABASE_URL.replace("sqlite+aiosqlite:", "sqlite:", 1)
    if settings.DATABASE_URL.startswith("postgresql+asyncpg://"):
        return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    return settings.DATABASE_URL
