"""Alembic environment for the enforcement hold-queue store.

The DB URL is read from ``-x url=...`` (offline), the CLI-set config, or the
``ENFORCEMENT_DATABASE_URL`` env. Async engine: Alembic runs migrations over a
sync connection inside ``run_sync``.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agent_capture_enforcement.config import get_settings
from agent_capture_enforcement.storage.models import Base

config = context.config

x_args = context.get_x_argument(as_dictionary=True)
dsn = (
    x_args.get("url")
    or config.get_main_option("sqlalchemy.url")
    or os.environ.get("ENFORCEMENT_DATABASE_URL")
    or get_settings().database_url
)
config.set_main_option("sqlalchemy.url", dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config({"sqlalchemy.url": dsn}, prefix="sqlalchemy.", future=True)
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
