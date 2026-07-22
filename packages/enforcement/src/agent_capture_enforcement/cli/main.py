"""Top-level ``enforcement`` CLI.

Phase 1 ships ``version`` and ``check-rules`` (validate a rule file). Later
phases add ``serve`` (verdict service) and ``db`` (hold-queue migrations).
"""

from __future__ import annotations

from typing import Any

import typer

from agent_capture_enforcement import __version__
from agent_capture_enforcement.rules import load_rules

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="agent-capture enforcement engine (layer 5)",
)


@app.callback()
def _main() -> None:
    """agent-capture enforcement engine (layer 5)."""


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command("check-rules")
def check_rules(path: str = typer.Argument(..., help="Path to a rule YAML file.")) -> None:
    """Validate a rule file and print a summary (fail-loud on malformed input)."""
    ruleset = load_rules(path)
    typer.secho(f"OK: {ruleset.version} — {len(ruleset.rules)} rule(s)", fg=typer.colors.GREEN)
    for r in ruleset.rules:
        typer.echo(f"  {r.id}: {r.span_type.value} → {r.evaluator} (mode={r.mode}, failure_mode={r.failure_mode})")


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Bind host (default ENFORCEMENT_LISTEN_HOST)."),
    port: int | None = typer.Option(None, help="Bind port (default ENFORCEMENT_LISTEN_PORT)."),
) -> None:
    """Run the inline verdict service."""
    import uvicorn

    from agent_capture_enforcement.config import get_settings
    from agent_capture_enforcement.service.app import create_app

    settings = get_settings()
    uvicorn.run(create_app(), host=host or settings.listen_host, port=port or settings.listen_port)


db_app = typer.Typer(no_args_is_help=True, help="Hold-queue database management.")
app.add_typer(db_app, name="db")


def _alembic_config(url: str | None = None) -> Any:
    """Build an Alembic config pointing at the bundled migrations (no .ini needed)."""
    from pathlib import Path

    from alembic.config import Config

    import agent_capture_enforcement

    migrations = Path(agent_capture_enforcement.__file__).resolve().parent / "storage" / "migrations"
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
    if url:
        cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@db_app.command("migrate")
def db_migrate(
    url: str | None = typer.Option(None, "--url", help="DSN; falls back to ENFORCEMENT_DATABASE_URL."),
) -> None:
    """Run Alembic upgrade head — the production schema path."""
    from alembic import command

    command.upgrade(_alembic_config(url), "head")
    typer.secho("ok: hold-queue schema at head", fg=typer.colors.GREEN)


@db_app.command("current")
def db_current(url: str | None = typer.Option(None, "--url")) -> None:
    """Show the current Alembic revision."""
    from alembic import command

    command.current(_alembic_config(url))


@db_app.command("downgrade")
def db_downgrade(revision: str = typer.Argument("-1"), url: str | None = typer.Option(None, "--url")) -> None:
    """Run Alembic downgrade (default: one revision)."""
    from alembic import command

    command.downgrade(_alembic_config(url), revision)


@db_app.command("init")
def db_init() -> None:
    """Create the hold-queue table via create_all (dev/tests only; prefer `db migrate`)."""
    import asyncio

    from agent_capture_enforcement.storage.engine import init_db

    asyncio.run(init_db())
    typer.secho("hold-queue schema is up to date", fg=typer.colors.GREEN)


@app.command("timeout-worker")
def timeout_worker(interval_s: int = typer.Option(60, help="Seconds between expiry sweeps.")) -> None:
    """Run the hold-timeout worker (expires pending holds past their deadline)."""
    import asyncio

    from agent_capture_enforcement.worker.timeout_job import run_forever

    asyncio.run(run_forever(interval_s))


if __name__ == "__main__":
    app()
