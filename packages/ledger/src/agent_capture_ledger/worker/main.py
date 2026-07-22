"""`ledger worker run` — start the retention + anchor + reconcile loop."""

from __future__ import annotations

import asyncio

import typer

from agent_capture_ledger.observability.logging import configure_logging

app = typer.Typer(no_args_is_help=True, help="Background workers")


@app.command("retention")
def retention() -> None:
    """Run the retention worker (sleep until configured hour daily)."""
    from agent_capture_ledger.config import get_settings
    from agent_capture_ledger.worker.retention_job import run_forever

    configure_logging(get_settings().log_level)
    asyncio.run(run_forever())


@app.command("anchor")
def anchor() -> None:
    """Run the attestation anchor loop."""
    from agent_capture_ledger.config import get_settings
    from agent_capture_ledger.worker.anchor_job import run_forever

    configure_logging(get_settings().log_level)
    asyncio.run(run_forever())


@app.command("anchor-now")
def anchor_now() -> None:
    """Run one attestation pass immediately."""
    from agent_capture_ledger.config import get_settings
    from agent_capture_ledger.worker.anchor_job import run_once

    configure_logging(get_settings().log_level)
    created = asyncio.run(run_once())
    typer.echo(f"attestations_created={created}")


@app.command("retention-now")
def retention_now() -> None:
    """Run one retention pass immediately (for ops / testing)."""
    from agent_capture_ledger.config import get_settings
    from agent_capture_ledger.retention.enforcer import run_retention
    from agent_capture_ledger.storage.engine import get_retention_engine

    configure_logging(get_settings().log_level)

    async def _go() -> None:
        engine = get_retention_engine()
        report = await run_retention(engine)
        typer.echo(f"partitions_dropped={report.partitions_dropped}")
        typer.echo(f"rows_deleted={report.rows_deleted}")

    asyncio.run(_go())
