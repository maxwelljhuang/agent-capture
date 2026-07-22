"""POST /verify/{trajectory_id} — full chain re-verification.

Recomputes ``content_hash`` from the stored ``body`` for every span and
walks ``parent_content_hash`` linkage. This is the catch-all: ``GET
/trajectories/{id}`` reports stored chain status; this endpoint actually
recomputes, which is what proves the body wasn't tampered.
"""

from __future__ import annotations

from agent_capture.schema import Span
from agent_capture.schema.canonical import content_hash
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, require_customer_scope, require_role
from agent_capture_ledger.api.errors import LedgerError
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import SpanRepo

router = APIRouter(prefix="/verify", tags=["read"])


@router.post("/{trajectory_id}")
async def verify_trajectory(
    trajectory_id: str,
    request: Request,
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, object]:
    rows = await SpanRepo(session).fetch_trajectory(trajectory_id)
    if not rows:
        raise LedgerError("LE404", 404, "Trajectory not found").http(f"unknown trajectory {trajectory_id}")
    require_customer_scope(token, rows[0].end_customer_id)

    findings: list[dict[str, str]] = []
    by_id = {r.span_id: r for r in rows}

    for r in rows:
        span = Span.model_validate(r.body)
        recomputed = content_hash(span)
        if recomputed != r.content_hash:
            findings.append(
                {
                    "span_id": r.span_id,
                    "kind": "content_hash_drift",
                    "stored": r.content_hash,
                    "recomputed": recomputed,
                }
            )
            continue
        if r.parent_span_id is None:
            if r.parent_content_hash is not None:
                findings.append({"span_id": r.span_id, "kind": "root_has_parent_hash"})
            continue
        parent = by_id.get(r.parent_span_id)
        if parent is None:
            findings.append({"span_id": r.span_id, "kind": "missing_parent"})
            continue
        if r.parent_content_hash != parent.content_hash:
            findings.append({"span_id": r.span_id, "kind": "parent_hash_mismatch"})

    status = "verified" if not findings else "broken"
    await AccessLogger(session, request, token).log(
        "verify.trajectory",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return {
        "trajectory_id": trajectory_id,
        "spans": len(rows),
        "status": status,
        "chain_intact": status == "verified",
        "findings": findings,
    }
