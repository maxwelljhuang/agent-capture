"""Periodic Merkle anchor job.

Every ``LEDGER_ATTESTATION_INTERVAL_SECONDS`` seconds, sweep each
end_customer_id and build/sign a window. Closed trajectories (root span
ended in the window) are leaves of that window's Merkle tree.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.config import get_settings
from agent_capture_ledger.integrity.attestation import (
    AttestationResult,
    build_window,
    export_to_file,
)
from agent_capture_ledger.integrity.signer import Signer, load_signer
from agent_capture_ledger.observability import metrics
from agent_capture_ledger.storage import models
from agent_capture_ledger.storage.engine import get_session_factory

log = structlog.get_logger(__name__)


def _load_signer() -> Signer:
    # KMS-backed signer when LEDGER_SIGNING_KMS_KEY is set, else the file signer.
    return load_signer()


async def _customers_with_closed_trajectories(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
) -> list[str]:
    rows = (
        await session.execute(
            select(models.Span.end_customer_id)
            .distinct()
            .where(
                models.Span.parent_span_id.is_(None),
                models.Span.end_time >= since,
                models.Span.end_time < until,
            )
        )
    ).all()
    return [r[0] for r in rows]


async def _last_window_end(session: AsyncSession, *, end_customer_id: str) -> datetime | None:
    row = (
        await session.execute(
            select(models.Attestation.window_end)
            .where(models.Attestation.end_customer_id == end_customer_id)
            .order_by(models.Attestation.window_end.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    assert isinstance(row, datetime)
    return row


async def _export(result: AttestationResult, sink_uri: str) -> bool:
    parsed = urlparse(sink_uri)
    try:
        if parsed.scheme == "file":
            await export_to_file(result, dir_path=Path(parsed.path))
            return True
        # webhook, s3 — stubs for now
        log.warning("attestation.sink.unsupported", scheme=parsed.scheme)
        return False
    except Exception as exc:
        log.error("attestation.export.failed", error=str(exc))
        metrics.attestation_export_failures.labels(sink=parsed.scheme).inc()
        return False


async def run_once(*, now: datetime | None = None) -> int:
    """One pass: build attestations for every customer with closed trajectories.

    Returns the number of attestations created.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    signer = _load_signer()
    created = 0

    factory = get_session_factory()
    async with factory() as session:
        # window: [last_window_end, now)
        customers = await _customers_with_closed_trajectories(
            session,
            since=now - timedelta(seconds=settings.attestation_interval_seconds * 10),
            until=now,
        )

    for customer in customers:
        async with factory() as session:
            last = await _last_window_end(session, end_customer_id=customer)
            window_start = last or (now - timedelta(seconds=settings.attestation_interval_seconds))
            result = await build_window(
                session,
                end_customer_id=customer,
                window_start=window_start,
                window_end=now,
                signer=signer,
            )
            if result is None:
                continue
            await session.commit()

            ok = await _export(result, settings.attestation_sink)
            if ok:
                async with factory() as s2:
                    await s2.execute(
                        text("UPDATE attestations SET exported_at = now() WHERE attestation_id = :aid").bindparams(
                            aid=result.attestation_id
                        )
                    )
                    await s2.commit()
            metrics.attestations_created.inc()
            created += 1
            log.info(
                "attestation.created",
                end_customer_id=customer,
                attestation_id=str(result.attestation_id),
                trajectories=result.trajectory_count,
                root=result.merkle_root,
            )

    return created


async def run_forever() -> None:
    settings = get_settings()
    interval = settings.attestation_interval_seconds
    while True:
        try:
            n = await run_once()
            log.info("attestation.pass.complete", created=n)
        except Exception as exc:
            log.error("attestation.pass.failed", error=str(exc))
        await asyncio.sleep(interval)
