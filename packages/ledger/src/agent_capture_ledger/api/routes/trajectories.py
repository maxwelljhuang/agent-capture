"""GET /trajectories — list, detail, span listing.

All reads enforce tenant scoping via ``require_customer_scope``. Each read
writes one ``access_log`` row before responding so the audit trail is
complete even if the caller drops mid-stream.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, effective_scope, require_customer_scope, require_role
from agent_capture_ledger.api.pagination import Cursor
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import SpanRepo

router = APIRouter(prefix="/trajectories", tags=["read"])


@router.get("")
async def list_trajectories(
    request: Request,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    regime: str | None = Query(None),
    type_: str | None = Query(None, alias="type"),
    status: str | None = Query(None),
    agent_version: str | None = Query(None),
    end_customer_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    # admin may filter to one tenant; reader is pinned to its own (param ignored).
    scope_customer = effective_scope(token, end_customer_id)
    c = Cursor.decode(cursor)
    cursor_tuple = (c.after_time, c.after_id) if c else None
    repo = SpanRepo(session)
    rows = await repo.list_trajectories(
        end_customer_id=scope_customer,
        from_time=from_time,
        to_time=to_time,
        regime=regime,
        type_=type_,
        status=status,
        agent_version=agent_version,
        cursor=cursor_tuple,
        limit=limit,
    )
    enriched = await repo.enrich_trajectories([tid for tid, *_ in rows])
    items = [
        {
            "trajectory_id": tid,
            "first_start": fs.isoformat(),
            "last_end": le.isoformat(),
            "span_count": n,
            "disposition": enriched.get(tid, {}).get("disposition"),
            "regulatory_regime": enriched.get(tid, {}).get("regulatory_regime", []),
            "subject_ref": enriched.get(tid, {}).get("subject_ref"),
        }
        for tid, fs, le, n in rows
    ]
    next_cursor: str | None = None
    if len(items) == limit and rows:
        last_tid, last_fs, _, _ = rows[-1]
        next_cursor = Cursor(after_time=last_fs, after_id=last_tid).encode()

    await AccessLogger(session, request, token).log(
        "list.trajectories",
        target_kind="trajectory",
        target_id="<list>",
    )
    await session.commit()
    return {"items": items, "next_cursor": next_cursor}


@router.get("/{trajectory_id}")
async def get_trajectory(
    trajectory_id: str,
    request: Request,
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    spans = await SpanRepo(session).fetch_trajectory(trajectory_id)
    if not spans:
        from agent_capture_ledger.api.errors import LedgerError

        raise LedgerError("LE404", 404, "Trajectory not found").http(f"unknown trajectory {trajectory_id}")
    customer = spans[0].end_customer_id
    require_customer_scope(token, customer)

    chain_status = _classify_chain(spans)
    await AccessLogger(session, request, token).log(
        "read.trajectory",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return {
        "trajectory_id": trajectory_id,
        "end_customer_id": customer,
        "span_count": len(spans),
        "first_start": spans[0].start_time.isoformat(),
        "last_end": max(s.end_time for s in spans).isoformat(),
        "chain_status": chain_status,
        "disposition": _disposition(spans),
    }


@router.get("/{trajectory_id}/spans")
async def get_trajectory_spans(
    trajectory_id: str,
    request: Request,
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    spans = await SpanRepo(session).fetch_trajectory(trajectory_id)
    if not spans:
        from agent_capture_ledger.api.errors import LedgerError

        raise LedgerError("LE404", 404, "Trajectory not found").http(f"unknown trajectory {trajectory_id}")
    require_customer_scope(token, spans[0].end_customer_id)

    items = [s.body for s in spans]
    await AccessLogger(session, request, token).log(
        "read.trajectory.spans",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return {"trajectory_id": trajectory_id, "spans": items}


def _disposition(spans: list[Any]) -> str:
    """Trajectory disposition derived from its ``policy_check`` spans.

    ``violation`` if any policy_check ``result == "fail"``; ``warn`` if any
    ``"warn"`` and no ``"fail"``; ``clean`` otherwise (``pass``/``not_applicable``
    and trajectories with no policy_check spans are clean). See
    ``docs/ledger-additive-plan.md``.
    """
    saw_warn = False
    for s in spans:
        if s.type != "policy_check":
            continue
        attrs = s.body.get("attributes") if isinstance(s.body, dict) else None
        result = attrs.get("result") if isinstance(attrs, dict) else None
        if result == "fail":
            return "violation"
        if result == "warn":
            saw_warn = True
    return "warn" if saw_warn else "clean"


def _classify_chain(spans: list[Any]) -> str:
    """Quick classification: verified / pending_parents / broken.

    Uses already-stored content_hash + parent_content_hash. The fuller
    re-verification (recompute every hash) lives behind POST /verify.
    """
    by_id = {s.span_id: s for s in spans}
    pending = False
    for s in spans:
        if s.parent_span_id is None:
            if s.parent_content_hash is not None:
                return "broken"
            continue
        parent = by_id.get(s.parent_span_id)
        if parent is None:
            pending = True
            continue
        if s.parent_content_hash != parent.content_hash:
            return "broken"
    return "incomplete:pending_parents" if pending else "verified"
