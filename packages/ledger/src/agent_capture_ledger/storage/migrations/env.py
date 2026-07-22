"""Alembic environment.

Sync migrations against the metadata in ``storage/models``. The DB URL is
read from the ``LEDGER_DATABASE_URL`` env or the ``-x url=...`` argument
when invoked offline. Async engine: Alembic uses a sync connection inside
``run_async``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from agent_capture_ledger.config import get_settings
from agent_capture_ledger.storage.models import Base

config = context.config

x_args = context.get_x_argument(as_dictionary=True)
# Priority: -x url=... > config sqlalchemy.url (set by CLI) >
# LEDGER_DATABASE_URL_MIGRATE > LEDGER_DATABASE_URL > settings default.
# CLI-set config wins over env so `ledger db migrate --url` works
# even when LEDGER_DATABASE_URL points at the runtime (non-DBA) role.
dsn = (
    x_args.get("url")
    or config.get_main_option("sqlalchemy.url")
    or os.environ.get("LEDGER_DATABASE_URL_MIGRATE")
    or os.environ.get("LEDGER_DATABASE_URL")
    or get_settings().database_url
)
config.set_main_option("sqlalchemy.url", dsn)

target_metadata = Base.metadata


def _include_object(
    obj: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    # The partitioned `spans` table is hand-DDL in the initial migration
    # because Alembic autogenerate cannot produce PARTITION BY clauses.
    return not (type_ == "table" and name == "spans")


def run_migrations_offline() -> None:
    context.configure(
        url=dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": dsn},
        prefix="sqlalchemy.",
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
