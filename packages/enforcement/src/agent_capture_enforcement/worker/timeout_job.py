"""Hold-timeout worker — expires pending holds past their deadline.

Mirrors the ledger's ``worker/retention_job.run_forever`` shape. A blocked
recorder client polling a hold's resolution sees ``timed_out`` once this runs
and applies the rule's timeout action (default: abort = block).
"""

from __future__ import annotations

import asyncio

import structlog

from agent_capture_enforcement.storage.engine import get_session_factory
from agent_capture_enforcement.storage.repository import HoldRepo

log = structlog.get_logger(__name__)


async def run_once() -> int:
    """Expire all due pending holds; return how many were timed out."""
    async with get_session_factory()() as session:
        count = await HoldRepo(session).expire_due()
        await session.commit()
    return count


async def run_forever(interval_s: int = 60) -> None:
    """Periodically expire due holds until cancelled."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            count = await run_once()
            if count:
                log.info("enforcement.holds_expired", count=count)
        except Exception as exc:  # a worker hiccup must not kill the loop
            log.error("enforcement.timeout_job_failed", error=str(exc))
