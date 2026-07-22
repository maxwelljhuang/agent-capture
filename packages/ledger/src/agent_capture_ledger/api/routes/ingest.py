"""POST /spans — recorder uploads a batch.

The hot path. Stateless; safe to scale horizontally. Every reject is
quarantined (not silently dropped) so an operator can browse rejections
later via ``GET /admin/quarantine``.

Per the plan, this endpoint matches HTTPExporter's wire shape exactly:
content-type application/json, body ``{"spans": [...]}``. The recorder's
batches are at-least-once; we de-dupe by ``(span_id, trajectory_id)``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from agent_capture.schema import Span
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, require_role
from agent_capture_ledger.api.errors import (
    LE001,
    LE002,
    LE003,
    LE004,
    LE005,
    LE006,
)
from agent_capture_ledger.config import get_settings
from agent_capture_ledger.integrity.verifier import check_content_hash
from agent_capture_ledger.observability import metrics
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import (
    BatchRepo,
    QuarantineRepo,
    SpanRepo,
)

router = APIRouter(tags=["ingest"])
log = structlog.get_logger(__name__)

# Optional layer-5 integration. Advisory enforcement runs at ingest only when
# the engine package is installed AND a rule file is configured; otherwise the
# ledger behaves exactly as before. The recorder/ledger never hard-depend on it.
_advisory_observe: Callable[..., object] | None
try:
    from agent_capture_enforcement.advisory import advisory_observe as _imported_observe

    _advisory_observe = _imported_observe
except ModuleNotFoundError:
    _advisory_observe = None


class SpansEnvelope(BaseModel):
    """Wire envelope produced by ``HTTPExporter._serialize``."""

    spans: list[dict[str, Any]]


class Rejection(BaseModel):
    span_id: str | None
    code: str
    detail: str | None = None


class IngestResponse(BaseModel):
    batch_id: str
    accepted: int
    rejected: list[Rejection]


@router.post("/spans", status_code=202, response_model=IngestResponse)
async def ingest_spans(
    envelope: SpansEnvelope,
    request: Request,
    token: Token = Depends(require_role("ingest")),
    session: AsyncSession = Depends(session_dependency),
) -> JSONResponse:
    batch_id = uuid.uuid4()
    started = time.monotonic()
    accepted: list[Span] = []
    rejected: list[Rejection] = []

    span_repo = SpanRepo(session)
    quarantine_repo = QuarantineRepo(session)
    settings = get_settings()
    supported_major = settings.schema_version_supported.split(".")[0]

    for raw in envelope.spans:
        rej = await _validate_one(
            raw,
            token=token,
            span_repo=span_repo,
            quarantine_repo=quarantine_repo,
            batch_id=batch_id,
            supported_major=supported_major,
            sink=accepted,
        )
        if rej is not None:
            rejected.append(rej)

    inserted = await span_repo.bulk_insert(accepted, batch_id=batch_id)

    await BatchRepo(session).record(
        batch_id=batch_id,
        source_token_id=token.token_id,
        end_customer_id=token.end_customer_id or "",
        span_count=len(envelope.spans),
        accepted=inserted,
        rejected=len(rejected),
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    await session.commit()

    metrics.ingest_batch.observe(len(envelope.spans))
    metrics.ingest_latency.observe(time.monotonic() - started)
    metrics.ingest_spans.labels(result="accepted", type="any").inc(inserted)
    metrics.ingest_spans.labels(result="rejected", type="any").inc(len(rejected))
    metrics.ingest_spans.labels(result="deduped", type="any").inc(len(accepted) - inserted)

    log.info(
        "ingest.batch",
        batch_id=str(batch_id),
        accepted=inserted,
        rejected=len(rejected),
        duration_ms=int((time.monotonic() - started) * 1000),
        actor_token_id=token.token_id,
        end_customer_id=token.end_customer_id,
    )

    # Advisory enforcement (layer 5) — off the response's critical path and
    # strictly observational: it can never reject or block an accepted span.
    if _advisory_observe is not None and accepted and settings.enforcement_rules_path is not None:
        try:
            _advisory_observe(accepted, rules_path=str(settings.enforcement_rules_path))
        except Exception:  # advisory must never break ingest
            log.warning("enforcement.advisory_error", exc_info=True)

    return JSONResponse(
        status_code=202,
        content={
            "batch_id": str(batch_id),
            "accepted": inserted,
            "rejected": [r.model_dump() for r in rejected],
        },
    )


async def _validate_one(
    raw: dict[str, Any],
    *,
    token: Token,
    span_repo: SpanRepo,
    quarantine_repo: QuarantineRepo,
    batch_id: uuid.UUID,
    supported_major: str,
    sink: list[Span],
) -> Rejection | None:
    raw_span_id = raw.get("span_id") if isinstance(raw, dict) else None

    # 1. shape
    try:
        span = Span.model_validate(raw)
    except ValidationError as e:
        await quarantine_repo.write(
            raw_body=raw,
            reason_code=LE001.code,
            reason_detail=str(e)[:2000],
            end_customer_id=token.end_customer_id,
            source_token_id=token.token_id,
            batch_id=batch_id,
        )
        metrics.chain_failures.labels(code=LE001.code).inc()
        return Rejection(span_id=raw_span_id, code=LE001.code, detail="shape invalid")

    # 2. tenancy — ingest token is scoped to one customer
    if token.end_customer_id and span.compliance.end_customer_id != token.end_customer_id:
        await quarantine_repo.write(
            raw_body=raw,
            reason_code=LE002.code,
            reason_detail=f"token={token.end_customer_id} span={span.compliance.end_customer_id}",
            end_customer_id=span.compliance.end_customer_id,
            source_token_id=token.token_id,
            batch_id=batch_id,
        )
        metrics.chain_failures.labels(code=LE002.code).inc()
        return Rejection(span_id=span.span_id, code=LE002.code, detail="tenant mismatch")

    # 3. schema version major check
    span_major = span.provenance.schema_version.split(".")[0]
    if span_major != supported_major:
        await quarantine_repo.write(
            raw_body=raw,
            reason_code=LE006.code,
            reason_detail=f"got {span.provenance.schema_version}, supported {supported_major}.x",
            end_customer_id=span.compliance.end_customer_id,
            source_token_id=token.token_id,
            batch_id=batch_id,
        )
        metrics.chain_failures.labels(code=LE006.code).inc()
        return Rejection(span_id=span.span_id, code=LE006.code, detail="schema major mismatch")

    # 4. integrity — recompute canonical hash
    check = check_content_hash(span)
    if not check.ok:
        await quarantine_repo.write(
            raw_body=raw,
            reason_code=LE003.code,
            reason_detail=f"expected={check.expected[:16]}… computed={check.computed[:16]}…",
            end_customer_id=span.compliance.end_customer_id,
            source_token_id=token.token_id,
            batch_id=batch_id,
        )
        metrics.chain_failures.labels(code=LE003.code).inc()
        return Rejection(span_id=span.span_id, code=LE003.code, detail="content_hash mismatch")

    # 5. idempotency
    existing = await span_repo.fetch(span.span_id, span.trajectory_id)
    if existing is not None:
        if existing.content_hash == check.computed:
            return None  # silent ack
        await quarantine_repo.write(
            raw_body=raw,
            reason_code=LE004.code,
            reason_detail="same (span_id, trajectory_id), different content_hash",
            end_customer_id=span.compliance.end_customer_id,
            source_token_id=token.token_id,
            batch_id=batch_id,
        )
        metrics.chain_failures.labels(code=LE004.code).inc()
        return Rejection(span_id=span.span_id, code=LE004.code, detail="immutability violation")

    # 6. parent linkage (best-effort; out-of-order tolerated)
    if span.parent_span_id is not None:
        parent = await span_repo.fetch(span.parent_span_id, span.trajectory_id)
        if parent is not None and span.provenance.parent_content_hash != parent.content_hash:
            await quarantine_repo.write(
                raw_body=raw,
                reason_code=LE005.code,
                reason_detail=f"parent.content_hash={parent.content_hash[:16]}… "
                f"span.parent_content_hash={(span.provenance.parent_content_hash or '')[:16]}…",
                end_customer_id=span.compliance.end_customer_id,
                source_token_id=token.token_id,
                batch_id=batch_id,
            )
            metrics.chain_failures.labels(code=LE005.code).inc()
            return Rejection(span_id=span.span_id, code=LE005.code, detail="parent hash mismatch")

    sink.append(span)
    return None
