"""`ledger db` — schema management."""

from __future__ import annotations

from pathlib import Path

import typer
from alembic import command
from alembic.config import Config

import agent_capture_ledger

app = typer.Typer(no_args_is_help=True, help="Database init + migrations")


def _migrations_dir() -> Path:
    """Return the path to the bundled migrations directory.

    Lives at ``<package>/storage/migrations/`` regardless of install
    location. Building config programmatically avoids needing an
    alembic.ini on disk inside the container.
    """
    return Path(agent_capture_ledger.__file__).resolve().parent / "storage" / "migrations"


def _config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_migrations_dir()))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@app.command("migrate")
def migrate(
    url: str | None = typer.Option(
        None, "--url", help="DB DSN. Falls back to LEDGER_DATABASE_URL_MIGRATE then LEDGER_DATABASE_URL."
    ),
) -> None:
    """Run Alembic upgrade head.

    Migrations require a role with CREATE on the public schema (table
    creation, trigger functions). The runtime ``LEDGER_DATABASE_URL`` is
    intentionally least-privileged and cannot run migrations. Set
    ``LEDGER_DATABASE_URL_MIGRATE`` to a DBA DSN, or pass ``--url``.
    """
    from agent_capture_ledger.config import get_settings

    effective = url or get_settings().database_url_migrate
    command.upgrade(_config(effective), "head")
    typer.echo("ok: schema at head")


@app.command("downgrade")
def downgrade(
    revision: str = typer.Argument("-1"),
    url: str | None = typer.Option(None, "--url"),
) -> None:
    """Run Alembic downgrade (default: one revision)."""
    command.downgrade(_config(url), revision)


@app.command("current")
def current(url: str | None = typer.Option(None, "--url")) -> None:
    """Show the current revision."""
    command.current(_config(url))


@app.command("init")
def init(url: str | None = typer.Option(None, "--url")) -> None:
    """Initialize a fresh DB: create roles + run migrations.

    Roles must be created via SQL because they're cluster-level objects.
    This step is idempotent — ``CREATE ROLE IF NOT EXISTS`` doesn't exist
    in Postgres, so we DO-block it.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from agent_capture_ledger.config import get_settings

    dsn = url or get_settings().database_url

    async def _create_roles() -> None:
        engine = create_async_engine(dsn, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                for role in ("ledger_app", "ledger_reader", "ledger_retention", "ledger_attestation"):
                    await conn.exec_driver_sql(
                        f"DO $$ BEGIN "
                        f"  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                        f"    CREATE ROLE {role} LOGIN; "
                        f"  END IF; "
                        f"END $$"
                    )
        finally:
            await engine.dispose()

    asyncio.run(_create_roles())
    command.upgrade(_config(url), "head")
    typer.echo("ok: roles + schema ready")
