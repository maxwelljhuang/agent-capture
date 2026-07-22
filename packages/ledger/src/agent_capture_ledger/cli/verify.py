"""`ledger verify <trajectory_id>` — re-verify a chain from the DB."""

from __future__ import annotations

import asyncio
import json

import typer

from agent_capture_ledger.storage.engine import get_session_factory


def verify_trajectory(trajectory_id: str = typer.Argument(...)) -> None:
    """Re-verify a trajectory's hash chain end-to-end."""
    from agent_capture.schema import Span
    from agent_capture.schema.canonical import content_hash

    from agent_capture_ledger.storage.repository import SpanRepo

    async def _go() -> dict[str, object]:
        async with get_session_factory()() as session:
            rows = await SpanRepo(session).fetch_trajectory(trajectory_id)
        if not rows:
            return {"status": "not_found", "trajectory_id": trajectory_id}
        by_id = {r.span_id: r for r in rows}
        findings = []
        for r in rows:
            span = Span.model_validate(r.body)
            recomputed = content_hash(span)
            if recomputed != r.content_hash:
                findings.append({"span_id": r.span_id, "kind": "content_hash_drift"})
                continue
            if r.parent_span_id is None:
                continue
            parent = by_id.get(r.parent_span_id)
            if parent is None:
                findings.append({"span_id": r.span_id, "kind": "missing_parent"})
                continue
            if r.parent_content_hash != parent.content_hash:
                findings.append({"span_id": r.span_id, "kind": "parent_hash_mismatch"})
        return {
            "trajectory_id": trajectory_id,
            "spans": len(rows),
            "status": "verified" if not findings else "broken",
            "chain_intact": not findings,
            "findings": findings,
        }

    out = asyncio.run(_go())
    typer.echo(json.dumps(out, indent=2))
    raise typer.Exit(code=0 if out.get("chain_intact") else 1)
