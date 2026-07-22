"""GET /access-log — read-only ledger read-audit (P5).

Surfaces who queried the ledger (actor token, role, action, target, request-id,
ip, ua, time) — metadata only, no span bodies. Tenant-scoped (admin may filter
with ?end_customer_id), cursor-paginated. Reading the access log is itself an
audited read. See docs/ledger-additive-plan.md §P5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, effective_scope, require_role
from agent_capture_ledger.api.pagination import Cursor
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import SpanRepo

router = APIRouter(tags=["read"])


@router.get("/access-log")
async def access_log(
    request: Request,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    end_customer_id: str | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    c = Cursor.decode(cursor)
    rows = await SpanRepo(session).list_access_log(
        end_customer_id=effective_scope(token, end_customer_id),  # admin may filter; reader pinned
        from_time=from_time,
        to_time=to_time,
        cursor=(c.after_time, c.after_id) if c else None,
        limit=limit,
    )
    items = [
        {
            "access_id": str(r.access_id),
            "actor_token_id": r.actor_token_id,
            "actor_role": r.actor_role,
            "end_customer_id": r.end_customer_id,
            "action": r.action,
            "target_kind": r.target_kind,
            "target_id": r.target_id,
            "at": r.at.isoformat(),
            "request_id": r.request_id,
            "ip": str(r.ip) if r.ip is not None else None,
            "user_agent": r.user_agent,
        }
        for r in rows
    ]
    next_cursor: str | None = None
    if len(rows) == limit and rows:
        last = rows[-1]
        next_cursor = Cursor(after_time=last.at, after_id=str(last.access_id)).encode()

    await AccessLogger(session, request, token).log("read.access_log", target_kind="access_log", target_id="<list>")
    await session.commit()
    return {"items": items, "next_cursor": next_cursor}
