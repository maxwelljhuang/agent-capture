"""GET /spans/{id} — single span."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, require_customer_scope, require_role
from agent_capture_ledger.api.errors import LedgerError
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import SpanRepo

router = APIRouter(prefix="/spans", tags=["read"])


@router.get("/{span_id}")
async def get_span(
    span_id: str,
    request: Request,
    trajectory_id: str = Query(
        ...,
        description="Required disambiguator (span_id alone is not unique across partitions).",
    ),
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, object]:
    span = await SpanRepo(session).fetch(span_id, trajectory_id)
    if span is None:
        raise LedgerError("LE404", 404, "Span not found").http(f"unknown span {span_id} in trajectory {trajectory_id}")
    require_customer_scope(token, span.end_customer_id)
    await AccessLogger(session, request, token).log(
        "read.span",
        target_kind="span",
        target_id=span_id,
    )
    await session.commit()
    return span.body
