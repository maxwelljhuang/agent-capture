"""Hold-queue repository. Callers own the transaction (commit)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_enforcement.storage.models import Hold

_TERMINAL = frozenset({"approved", "rejected", "timed_out", "aborted"})


def _now() -> datetime:
    return datetime.now(tz=UTC)


class HoldRepo:
    """CRUD + state transitions for the fail-to-human hold queue."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        *,
        end_customer_id: str,
        trajectory_id: str,
        span_id: str,
        policy_name: str,
        policy_version: str,
        rule_id: str,
        reason: str = "",
        proposed_action: str = "",
        parent_content_hash: str | None = None,
        hold_timeout_s: int = 3600,
        hold_id: str | None = None,
    ) -> Hold:
        """Create a pending hold and flush it (caller commits)."""
        now = _now()
        hold = Hold(
            hold_id=hold_id or str(uuid.uuid4()),
            end_customer_id=end_customer_id,
            trajectory_id=trajectory_id,
            span_id=span_id,
            parent_content_hash=parent_content_hash,
            policy_name=policy_name,
            policy_version=policy_version,
            rule_id=rule_id,
            proposed_action=proposed_action,
            status="pending",
            reason=reason,
            created_at=now,
            expires_at=now + timedelta(seconds=hold_timeout_s),
        )
        self._s.add(hold)
        await self._s.flush()
        return hold

    async def get(self, hold_id: str) -> Hold | None:
        return await self._s.get(Hold, hold_id)

    async def list_pending(self, end_customer_id: str) -> list[Hold]:
        rows = await self._s.execute(
            select(Hold)
            .where(Hold.end_customer_id == end_customer_id, Hold.status == "pending")
            .order_by(Hold.created_at)
        )
        return list(rows.scalars().all())

    async def count_pending(self, end_customer_id: str) -> int:
        """Number of pending holds for a tenant — cheap tile source (avoids paging)."""
        result = await self._s.execute(
            select(func.count())
            .select_from(Hold)
            .where(Hold.end_customer_id == end_customer_id, Hold.status == "pending")
        )
        return int(result.scalar_one())

    async def resolve(
        self,
        hold_id: str,
        *,
        decision: str,
        approver_token_id: str | None = None,
        decision_reason: str = "",
    ) -> Hold | None:
        """Resolve a *pending* hold. Returns None if missing or already terminal."""
        hold = await self._s.get(Hold, hold_id)
        if hold is None or hold.status != "pending":
            return None
        hold.status = decision
        hold.approver_token_id = approver_token_id
        hold.decision_reason = decision_reason
        hold.resolved_at = _now()
        await self._s.flush()
        return hold

    async def expire_due(self, *, now: datetime | None = None) -> int:
        """Mark every pending hold past its ``expires_at`` as ``timed_out``."""
        cutoff = now or _now()
        result = await self._s.execute(
            update(Hold)
            .where(Hold.status == "pending", Hold.expires_at <= cutoff)
            .values(status="timed_out", resolved_at=cutoff, decision_reason="hold expired")
        )
        await self._s.flush()
        return int(cast("CursorResult[Any]", result).rowcount or 0)
