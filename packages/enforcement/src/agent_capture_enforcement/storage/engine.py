"""Async engine + session factory for the hold-queue store.

Works with asyncpg (production) and aiosqlite (tests) — the hold table is a
plain table with no Postgres-specific DDL, so SQLite is a faithful test double.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from agent_capture_enforcement.config import get_settings
from agent_capture_enforcement.storage.models import Base

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use.

    Uses :class:`NullPool` so connections are never held across event loops —
    the hold queue is low-volume, and this keeps the engine safe under test
    harnesses (e.g. Starlette's TestClient portal) that run on a fresh loop.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, future=True, poolclass=NullPool)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _factory


async def reset_engines() -> None:
    """Dispose the engine + factory (tests, or after a settings change)."""
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


async def init_db() -> None:
    """Create the hold table if it does not exist (engine self-bootstrap)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session per request."""
    async with get_session_factory()() as session:
        yield session
