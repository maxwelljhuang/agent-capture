"""Litigation hold endpoints — admin only.

A hold on a trajectory prevents the retention worker from deleting any of
its spans, regardless of TTL. When released, the trajectory becomes
eligible again on the next pass.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, require_role
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage import models
from agent_capture_ledger.storage.engine import session_dependency

router = APIRouter(prefix="/litigation-holds", tags=["admin"])


class PlaceHoldRequest(BaseModel):
    reason: str | None = None


@router.post("/{trajectory_id}", status_code=201)
async def place_hold(
    trajectory_id: str,
    body: PlaceHoldRequest,
    request: Request,
    token: Token = Depends(require_role("admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, str]:
    existing = (
        await session.execute(select(models.LitigationHold).where(models.LitigationHold.trajectory_id == trajectory_id))
    ).scalar_one_or_none()
    if existing is not None and existing.released_at is None:
        return {"status": "already_held", "trajectory_id": trajectory_id}
    if existing is not None:
        # re-place by clearing release timestamp
        existing.released_at = None
        existing.released_by = None
        existing.placed_by = token.token_id
        existing.reason = body.reason
        existing.placed_at = datetime.now(UTC)
    else:
        session.add(
            models.LitigationHold(
                trajectory_id=trajectory_id,
                placed_by=token.token_id,
                reason=body.reason,
            )
        )
    await AccessLogger(session, request, token).log(
        "place.hold",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return {"status": "held", "trajectory_id": trajectory_id}


@router.delete("/{trajectory_id}")
async def release_hold(
    trajectory_id: str,
    request: Request,
    token: Token = Depends(require_role("admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, str]:
    row = (
        await session.execute(select(models.LitigationHold).where(models.LitigationHold.trajectory_id == trajectory_id))
    ).scalar_one_or_none()
    if row is None or row.released_at is not None:
        from agent_capture_ledger.api.errors import LedgerError

        raise LedgerError("LE404", 404, "Hold not found").http(f"no active hold for {trajectory_id}")
    row.released_at = datetime.now(UTC)
    row.released_by = token.token_id
    await AccessLogger(session, request, token).log(
        "release.hold",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return {"status": "released", "trajectory_id": trajectory_id}


@router.get("")
async def list_holds(
    request: Request,
    token: Token = Depends(require_role("admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, object]:
    rows = (
        (
            await session.execute(
                select(models.LitigationHold)
                .where(models.LitigationHold.released_at.is_(None))
                .order_by(models.LitigationHold.placed_at.desc())
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "trajectory_id": r.trajectory_id,
            "placed_by": r.placed_by,
            "reason": r.reason,
            "placed_at": r.placed_at.isoformat(),
        }
        for r in rows
    ]
    await AccessLogger(session, request, token).log(
        "list.holds",
        target_kind="hold",
        target_id="<list>",
    )
    await session.commit()
    return {"items": items}
