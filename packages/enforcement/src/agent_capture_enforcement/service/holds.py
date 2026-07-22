"""Hold review API — the reviewer surface for the fail-to-human path.

Two audiences:
- **Reviewers** (humans / their tooling) list pending holds and resolve them.
  Gated by the reviewer token + scoped by ``end_customer_id``.
- **The recorder client** polls a held action's resolution to learn whether to
  run or abort it. Gated by the service token (same as ``/verdict``).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_enforcement.service.auth import require_reviewer_token, require_service_token
from agent_capture_enforcement.storage.engine import session_dependency
from agent_capture_enforcement.storage.models import Hold
from agent_capture_enforcement.storage.repository import HoldRepo

router = APIRouter(tags=["holds"])
log = structlog.get_logger(__name__)

# A terminal hold maps to a gate decision for the blocked recorder caller.
_RESOLUTION_DECISION = {
    "approved": "allow",
    "rejected": "block",
    "timed_out": "block",
    "aborted": "block",
}


class HoldView(BaseModel):
    hold_id: str
    end_customer_id: str
    trajectory_id: str
    span_id: str
    rule_id: str
    policy_version: str
    proposed_action: str
    reason: str
    status: str

    @classmethod
    def of(cls, h: Hold) -> HoldView:
        return cls(
            hold_id=h.hold_id,
            end_customer_id=h.end_customer_id,
            trajectory_id=h.trajectory_id,
            span_id=h.span_id,
            rule_id=h.rule_id,
            policy_version=h.policy_version,
            proposed_action=h.proposed_action,
            reason=h.reason,
            status=h.status,
        )


class ResolveRequest(BaseModel):
    decision: str  # "approved" | "rejected"
    decision_reason: str = ""
    approver_identity: str | None = None


class ResolutionView(BaseModel):
    hold_id: str
    status: str
    decision: str | None  # gate decision once terminal: allow|block, else None


@router.get("/holds")
async def list_holds(
    end_customer_id: str,
    reviewer_customer: str | None = Depends(require_reviewer_token),
    session: AsyncSession = Depends(session_dependency),
) -> list[HoldView]:
    """List pending holds for one tenant (reviewer-scoped)."""
    if reviewer_customer is not None and end_customer_id != reviewer_customer:
        raise HTTPException(status_code=403, detail="reviewer not scoped to this tenant")
    holds = await HoldRepo(session).list_pending(end_customer_id)
    return [HoldView.of(h) for h in holds]


@router.get("/holds/count")
async def count_holds(
    end_customer_id: str,
    reviewer_customer: str | None = Depends(require_reviewer_token),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, int]:
    """Pending-hold count for one tenant — cheap tile source (P3)."""
    if reviewer_customer is not None and end_customer_id != reviewer_customer:
        raise HTTPException(status_code=403, detail="reviewer not scoped to this tenant")
    return {"pending": await HoldRepo(session).count_pending(end_customer_id)}


@router.post("/holds/{hold_id}/resolve")
async def resolve_hold(
    hold_id: str,
    body: ResolveRequest,
    reviewer_customer: str | None = Depends(require_reviewer_token),
    session: AsyncSession = Depends(session_dependency),
) -> HoldView:
    """Approve or reject a pending hold."""
    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    repo = HoldRepo(session)
    if reviewer_customer is not None:
        # A tenant-bound reviewer may only resolve its own tenant's holds.
        existing = await repo.get(hold_id)
        if existing is not None and existing.end_customer_id != reviewer_customer:
            raise HTTPException(status_code=403, detail="reviewer not scoped to this tenant")
    hold = await repo.resolve(
        hold_id,
        decision=body.decision,
        approver_token_id=body.approver_identity,
        decision_reason=body.decision_reason,
    )
    if hold is None:
        raise HTTPException(status_code=409, detail="hold not found or already resolved")
    await session.commit()
    log.info(
        "enforcement.hold_resolved",
        hold_id=hold_id,
        decision=body.decision,
        end_customer_id=hold.end_customer_id,
        rule_id=hold.rule_id,
        approver=body.approver_identity,
    )
    return HoldView.of(hold)


@router.get("/holds/{hold_id}/resolution", dependencies=[Depends(require_service_token)])
async def hold_resolution(
    hold_id: str,
    session: AsyncSession = Depends(session_dependency),
) -> ResolutionView:
    """Poll a hold's resolution (called by the blocked recorder client)."""
    hold = await HoldRepo(session).get(hold_id)
    if hold is None:
        raise HTTPException(status_code=404, detail="unknown hold")
    decision = _RESOLUTION_DECISION.get(hold.status)
    return ResolutionView(hold_id=hold_id, status=hold.status, decision=decision)
