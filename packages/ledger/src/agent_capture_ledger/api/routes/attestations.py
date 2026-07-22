"""GET /attestations — list, detail, inclusion proofs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, require_customer_scope, require_role
from agent_capture_ledger.api.errors import LedgerError
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.integrity.attestation import proof_for
from agent_capture_ledger.storage import models
from agent_capture_ledger.storage.engine import session_dependency

router = APIRouter(prefix="/attestations", tags=["read"])


@router.get("")
async def list_attestations(
    request: Request,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    conditions: list[Any] = []
    if token.end_customer_id is not None:
        conditions.append(models.Attestation.end_customer_id == token.end_customer_id)
    if from_time:
        conditions.append(models.Attestation.window_end >= from_time)
    if to_time:
        conditions.append(models.Attestation.window_end <= to_time)
    stmt = select(models.Attestation)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.order_by(models.Attestation.window_end.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()

    await AccessLogger(session, request, token).log(
        "list.attestations",
        target_kind="attestation",
        target_id="<list>",
    )
    await session.commit()
    return {
        "items": [
            {
                "attestation_id": str(r.attestation_id),
                "end_customer_id": r.end_customer_id,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "trajectory_count": r.trajectory_count,
                "merkle_root": r.merkle_root,
                "signing_key_id": r.signing_key_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/{attestation_id}")
async def get_attestation(
    attestation_id: str,
    request: Request,
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    row = (
        await session.execute(select(models.Attestation).where(models.Attestation.attestation_id == attestation_id))
    ).scalar_one_or_none()
    if row is None:
        raise LedgerError("LE404", 404, "Attestation not found").http(f"unknown attestation {attestation_id}")
    require_customer_scope(token, row.end_customer_id)
    leaves = (
        (
            await session.execute(
                select(models.AttestationLeaf)
                .where(models.AttestationLeaf.attestation_id == attestation_id)
                .order_by(models.AttestationLeaf.leaf_index)
            )
        )
        .scalars()
        .all()
    )

    await AccessLogger(session, request, token).log(
        "read.attestation",
        target_kind="attestation",
        target_id=attestation_id,
    )
    await session.commit()
    return {
        "attestation_id": str(row.attestation_id),
        "end_customer_id": row.end_customer_id,
        "window_start": row.window_start.isoformat(),
        "window_end": row.window_end.isoformat(),
        "merkle_root": row.merkle_root,
        "signature": bytes(row.signature).hex(),
        "signing_key_id": row.signing_key_id,
        "leaves": [
            {
                "trajectory_id": leaf.trajectory_id,
                "trajectory_root_hash": leaf.trajectory_root_hash,
                "leaf_index": leaf.leaf_index,
            }
            for leaf in leaves
        ],
    }


@router.get("/proof/{trajectory_id}")
async def attestation_proof(
    trajectory_id: str,
    request: Request,
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    proof = await proof_for(session, trajectory_id=trajectory_id)
    if proof is None:
        raise LedgerError("LE404", 404, "No attestation covers this trajectory yet").http(
            f"trajectory {trajectory_id} not in any attestation"
        )
    # tenant scope: pull the attestation to enforce
    attestation = (
        await session.execute(
            select(models.Attestation).where(models.Attestation.attestation_id == proof["attestation_id"])
        )
    ).scalar_one()
    require_customer_scope(token, attestation.end_customer_id)

    await AccessLogger(session, request, token).log(
        "read.attestation.proof",
        target_kind="trajectory",
        target_id=trajectory_id,
    )
    await session.commit()
    return proof
