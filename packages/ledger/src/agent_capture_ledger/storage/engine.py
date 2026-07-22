"""Async SQLAlchemy engine + session factories.

Two engines: the default application engine (``ledger_app`` role; can INSERT
and SELECT, can't UPDATE/DELETE — the append-only trigger enforces) and the
retention engine (``ledger_retention`` role; the only role allowed to delete
spans). The retention engine is lazily constructed and only used by the
retention worker.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from agent_capture_ledger.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

_retention_engine: AsyncEngine | None = None
_retention_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(dsn: str, *, settings: Settings) -> AsyncEngine:
    # Tests cross event loops (pytest-asyncio + FastAPI TestClient run on
    # different loops than fixture setup). asyncpg connections are bound
    # to the loop they're opened on, so the default QueuePool breaks with
    # "Future attached to a different loop". NullPool fixes this by
    # creating a fresh connection per checkout.
    if os.environ.get("LEDGER_TEST_DATABASE_URL"):
        return create_async_engine(dsn, poolclass=NullPool, future=True)
    return create_async_engine(
        dsn,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = _build_engine(settings.database_url, settings=settings)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


def get_retention_engine() -> AsyncEngine:
    global _retention_engine, _retention_session_factory
    if _retention_engine is None:
        settings = get_settings()
        dsn = settings.database_url_retention or settings.database_url
        _retention_engine = _build_engine(dsn, settings=settings)
        _retention_session_factory = async_sessionmaker(_retention_engine, expire_on_commit=False)
    return _retention_engine


def get_retention_session_factory() -> async_sessionmaker[AsyncSession]:
    if _retention_session_factory is None:
        get_retention_engine()
    assert _retention_session_factory is not None
    return _retention_session_factory


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def reset_engines() -> None:
    """Tests: dispose engines so a new settings singleton takes effect."""
    global _engine, _session_factory, _retention_engine, _retention_session_factory
    if _engine is not None:
        await _engine.dispose()
    if _retention_engine is not None:
        await _retention_engine.dispose()
    _engine = None
    _session_factory = None
    _retention_engine = None
    _retention_session_factory = None
