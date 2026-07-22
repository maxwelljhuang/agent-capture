"""Periodic retention runner.

Sleeps until ``LEDGER_RETENTION_HOUR_UTC`` each day, runs a full pass,
goes back to sleep. The job uses the dedicated ``ledger_retention``
engine so DELETE/DROP succeed past the append-only trigger.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from agent_capture_ledger.config import get_settings
from agent_capture_ledger.retention.enforcer import run_retention
from agent_capture_ledger.storage.engine import get_retention_engine

log = structlog.get_logger(__name__)


def _seconds_until_hour(hour_utc: int, *, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


async def run_forever() -> None:
    settings = get_settings()
    engine = get_retention_engine()
    while True:
        delay = _seconds_until_hour(settings.retention_hour_utc)
        log.info("retention.sleep", seconds=int(delay), hour_utc=settings.retention_hour_utc)
        await asyncio.sleep(delay)
        try:
            report = await run_retention(engine)
            log.info(
                "retention.complete", partitions_dropped=report.partitions_dropped, rows_deleted=report.rows_deleted
            )
        except Exception as exc:
            log.error("retention.failed", error=str(exc))
