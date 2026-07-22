"""Shared fixtures.

Integration tests need a real Postgres because:

- the ``spans`` table is PARTITIONED — SQLite can't run that DDL,
- ``JSONB`` + ``GIN`` indexes + array types only exist in Postgres,
- the append-only TRIGGER is plpgsql.

We use ``testcontainers[postgres]`` so a clean container spins up per
session. Tests marked ``integration`` (or ``e2e``) get the live DSN.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agent_capture_ledger.config import Settings, set_settings
from agent_capture_ledger.storage import engine as engine_mod


def _run_migrations(dsn: str) -> None:
    """Run ``alembic upgrade head`` in a subprocess.

    Cannot call ``alembic.command.upgrade`` in-process here because
    ``env.py`` uses ``asyncio.run(...)`` at module load, which crashes
    inside pytest's already-running event loop.
    """
    from pathlib import Path

    pkg_root = Path(__file__).resolve().parent.parent
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(pkg_root / "alembic.ini"), "upgrade", "head"],
        env={**os.environ, "LEDGER_DATABASE_URL": dsn, "PYTHONPATH": str(pkg_root)},
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def postgres_dsn() -> Iterator[str]:
    """Yield a Postgres DSN. Skips if Docker is unavailable."""
    if os.environ.get("LEDGER_TEST_DATABASE_URL"):
        yield os.environ["LEDGER_TEST_DATABASE_URL"]
        return
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer(
        image="postgres:16-alpine",
        username="postgres",
        password="postgres",
        dbname="ledger",
    )
    try:
        container.start()
    except Exception as exc:  # docker missing
        pytest.skip(f"docker unavailable: {exc}")

    sync_dsn = container.get_connection_url()
    # testcontainers returns psycopg2 dsn; swap for asyncpg
    async_dsn = sync_dsn.replace("postgresql+psycopg2", "postgresql+asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    try:
        yield async_dsn
    finally:
        container.stop()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def migrated_dsn(postgres_dsn: str) -> AsyncIterator[str]:
    """Bring schema to head once per session.

    Creates the ``ledger_retention`` role too, since the append-only
    trigger references it. The session runs as superuser so all roles
    can be created without bootstrapping.
    """
    engine = create_async_engine(postgres_dsn, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        for role in ("ledger_app", "ledger_reader", "ledger_retention", "ledger_attestation"):
            await conn.exec_driver_sql(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') "
                f"THEN CREATE ROLE {role}; END IF; END $$"
            )
    await engine.dispose()
    _run_migrations(postgres_dsn)

    # After migration: grant privileges. ALTER DEFAULT PRIVILEGES only
    # affects future tables, but the migration just created them, so we
    # GRANT explicitly here.
    engine = create_async_engine(postgres_dsn, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO ledger_app")
        await conn.exec_driver_sql("GRANT SELECT ON ALL TABLES IN SCHEMA public TO ledger_reader, ledger_attestation")
        await conn.exec_driver_sql("GRANT INSERT ON access_log TO ledger_reader")
        await conn.exec_driver_sql("GRANT INSERT ON attestations, attestation_leaves TO ledger_attestation")
        await conn.exec_driver_sql(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ledger_retention"
        )
    await engine.dispose()
    yield postgres_dsn


@pytest_asyncio.fixture
async def session(migrated_dsn: str) -> AsyncIterator[AsyncSession]:
    """Per-test SQLAlchemy session with truncation between tests."""
    set_settings(Settings(database_url=migrated_dsn))
    await engine_mod.reset_engines()
    factory = engine_mod.get_session_factory()
    # truncate user tables for isolation; spans is partitioned so
    # TRUNCATE cascades to partitions
    async with factory() as s:
        await s.execute(
            text(
                "TRUNCATE spans, ingest_batches, quarantine, attestations, "
                "attestation_leaves, access_log, api_tokens, litigation_holds, "
                "retention_operations RESTART IDENTITY CASCADE"
            )
        )
        await s.commit()
    async with factory() as s:
        yield s
    await engine_mod.reset_engines()
