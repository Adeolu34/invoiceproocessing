"""SQLAlchemy async engine, session factory, and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Neon / Supabase / cloud providers append ?sslmode=require to the URL.
# asyncpg needs ssl passed as a connect_arg instead of a query param.
_db_url = settings.database_url
_connect_args: dict = {}
if "sslmode=require" in _db_url:
    _db_url = _db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = "require"

# Create async engine — pool_pre_ping keeps connections alive across restarts
engine = create_async_engine(
    _db_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.log_level == "DEBUG",
    connect_args=_connect_args,
)

# Session factory — expire_on_commit=False avoids lazy-load errors after commit
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models."""


async def init_db() -> None:
    """Create all tables. Safe to call on every startup (CREATE IF NOT EXISTS)."""
    # Import models so they register with Base.metadata before create_all
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
