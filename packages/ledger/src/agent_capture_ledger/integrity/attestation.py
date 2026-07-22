"""Build → sign → store attestation windows.

Per-customer windows. The window collects all trajectories whose root span
has an end_time in [window_start, window_end). For each, compute the
trajectory root hash (Merkle over span content_hashes in start_time
order). Then Merkle over trajectory roots → window root → sign.

The signed payload is canonical: ``root || window_end ISO || customer``,
ASCII-encoded. Determinism matters because verifiers will recompute and
compare bytes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.integrity import merkle
from agent_capture_ledger.integrity.signer import Signer
from agent_capture_ledger.storage import models


@dataclass(frozen=True)
class AttestationResult:
    attestation_id: uuid.UUID
    end_customer_id: str
    window_start: datetime
    window_end: datetime
    trajectory_count: int
    merkle_root: str
    signature: bytes
    signing_key_id: str
    leaf_pairs: list[tuple[str, str]]  # (trajectory_id, root_hash)


def signing_payload(*, root: str, window_end: datetime, end_customer_id: str) -> bytes:
    """Deterministic ASCII bytes signed by Ed25519."""
    return f"{root}|{window_end.isoformat()}|{end_customer_id}".encode("ascii")


async def build_window(
    session: AsyncSession,
    *,
    end_customer_id: str,
    window_start: datetime,
    window_end: datetime,
    signer: Signer,
) -> AttestationResult | None:
    """Build, sign, and persist an attestation. Returns ``None`` if empty.

    A trajectory qualifies for the window iff its **root** span's
    ``end_time`` falls in ``[window_start, window_end)``. Practically,
    "the trajectory closed in this window."
    """
    rows = (
        await session.execute(
            text(
                """
        SELECT trajectory_id, span_id, content_hash, start_time, parent_span_id, end_time
        FROM spans
        WHERE end_customer_id = :cid
          AND trajectory_id IN (
            SELECT trajectory_id FROM spans
            WHERE end_customer_id = :cid
              AND parent_span_id IS NULL
              AND end_time >= :ws
              AND end_time <  :we
          )
        ORDER BY trajectory_id, start_time, span_id
        """
            ).bindparams(cid=end_customer_id, ws=window_start, we=window_end)
        )
    ).all()

    if not rows:
        return None

    # Group spans by trajectory, compute per-trajectory roots
    by_traj: dict[str, list[str]] = {}
    for r in rows:
        by_traj.setdefault(r.trajectory_id, []).append(r.content_hash)

    leaf_pairs = [(tid, merkle.trajectory_root(hashes)) for tid, hashes in sorted(by_traj.items())]
    window_root = merkle.build_root([root for _, root in leaf_pairs])

    payload = signing_payload(root=window_root, window_end=window_end, end_customer_id=end_customer_id)
    signature = signer.sign(payload)

    attestation_id = uuid.uuid4()
    session.add(
        models.Attestation(
            attestation_id=attestation_id,
            end_customer_id=end_customer_id,
            window_start=window_start,
            window_end=window_end,
            trajectory_count=len(leaf_pairs),
            leaf_hashes_count=len(leaf_pairs),
            merkle_root=window_root,
            signature=signature,
            signing_key_id=signer.key_id,
        )
    )
    for idx, (tid, root) in enumerate(leaf_pairs):
        session.add(
            models.AttestationLeaf(
                attestation_id=attestation_id,
                trajectory_id=tid,
                trajectory_root_hash=root,
                leaf_index=idx,
            )
        )

    return AttestationResult(
        attestation_id=attestation_id,
        end_customer_id=end_customer_id,
        window_start=window_start,
        window_end=window_end,
        trajectory_count=len(leaf_pairs),
        merkle_root=window_root,
        signature=signature,
        signing_key_id=signer.key_id,
        leaf_pairs=leaf_pairs,
    )


def attestation_envelope(result: AttestationResult) -> dict[str, object]:
    """Serializable form for external sinks."""
    return {
        "attestation_id": str(result.attestation_id),
        "end_customer_id": result.end_customer_id,
        "window_start": result.window_start.isoformat(),
        "window_end": result.window_end.isoformat(),
        "merkle_root": result.merkle_root,
        "signature": result.signature.hex(),
        "signing_key_id": result.signing_key_id,
        "trajectories": [{"trajectory_id": tid, "trajectory_root_hash": root} for tid, root in result.leaf_pairs],
    }


async def proof_for(
    session: AsyncSession,
    *,
    trajectory_id: str,
) -> dict[str, object] | None:
    """Return Merkle inclusion proof for ``trajectory_id``'s most-recent attestation."""
    leaf_row = (
        await session.execute(
            select(models.AttestationLeaf)
            .where(models.AttestationLeaf.trajectory_id == trajectory_id)
            .order_by(models.AttestationLeaf.attestation_id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if leaf_row is None:
        return None

    attestation = (
        await session.execute(
            select(models.Attestation).where(models.Attestation.attestation_id == leaf_row.attestation_id)
        )
    ).scalar_one()

    all_leaves = (
        (
            await session.execute(
                select(models.AttestationLeaf)
                .where(models.AttestationLeaf.attestation_id == attestation.attestation_id)
                .order_by(models.AttestationLeaf.leaf_index)
            )
        )
        .scalars()
        .all()
    )
    leaf_hashes = [r.trajectory_root_hash for r in all_leaves]

    proof = merkle.build_proof(leaf_hashes, leaf_row.leaf_index)
    return {
        "attestation_id": str(attestation.attestation_id),
        "merkle_root": attestation.merkle_root,
        "trajectory_root_hash": leaf_row.trajectory_root_hash,
        "leaf_index": leaf_row.leaf_index,
        "leaf_count": proof.leaf_count,
        "path": [{"sibling": s.sibling, "side": s.side} for s in proof.path],
        "window_end": attestation.window_end.isoformat(),
        "signing_key_id": attestation.signing_key_id,
        "signature": bytes(attestation.signature).hex(),
    }


async def export_to_file(result: AttestationResult, *, dir_path: str | Path) -> None:
    """Write a JSON-line attestation file. Sink failure isn't fatal."""
    p = Path(dir_path)
    p.mkdir(parents=True, exist_ok=True)
    out = p / f"{result.attestation_id}.json"
    out.write_text(json.dumps(attestation_envelope(result), separators=(",", ":")))
