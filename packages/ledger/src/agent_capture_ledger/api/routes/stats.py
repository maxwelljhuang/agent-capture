"""GET /stats — aggregate counts + dashboard metrics for the query API.

Tenant-scoped, audited. The monitoring primitive an external dashboard's
backend queries over the wire: only counts leave the ledger, never span bodies.
Computed in-place via SQL ``GROUP BY``.

Time windowing (P7): pass ``window`` (e.g. ``7d``, ``30d``, ``24h``) and/or
``from``/``to`` ISO timestamps to scope the dashboard aggregates to trajectories
whose ``first_start`` falls in the window, plus a ``previous`` object over the
immediately-preceding equal-length window for trend deltas. Omitting all of them
preserves the v1.1 all-time response.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token, effective_scope, require_role
from agent_capture_ledger.api.errors import LE007
from agent_capture_ledger.audit.access_log import AccessLogger
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import SpanRepo

router = APIRouter(tags=["read"])

_DURATION_RE = re.compile(r"^(\d+)(h|d|w)$")
_UNITS = {"h": "hours", "d": "days", "w": "weeks"}


def _parse_duration(window: str) -> timedelta:
    match = _DURATION_RE.match(window.strip().lower())
    if match is None:
        raise LE007.http(f"invalid window {window!r}; use e.g. 24h, 7d, 4w")
    amount = int(match.group(1))
    if amount <= 0:
        raise LE007.http("window must be a positive duration")
    return timedelta(**{_UNITS[match.group(2)]: amount})


def _parse_dt(raw: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LE007.http(f"invalid {name} timestamp {raw!r}; expected ISO-8601") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _resolve_window(
    window: str | None, from_raw: str | None, to_raw: str | None
) -> tuple[datetime | None, datetime | None, str | None, bool]:
    """→ (from_eff, to_eff, window_label, with_previous). Raises LE007 (400) on invalid input."""
    from_t = _parse_dt(from_raw, "from") if from_raw else None
    to_t = _parse_dt(to_raw, "to") if to_raw else None

    from_eff: datetime | None
    to_eff: datetime | None
    label: str | None
    if window is not None:
        delta = _parse_duration(window)
        to_eff = to_t or datetime.now(UTC)
        from_eff = to_eff - delta
        label = window
    else:
        from_eff, to_eff, label = from_t, to_t, None

    if from_eff is not None and to_eff is not None and from_eff > to_eff:
        raise LE007.http("`from` must be on or before `to`")
    with_previous = from_eff is not None and to_eff is not None
    return from_eff, to_eff, label, with_previous


@router.get("/stats")
async def stats(
    request: Request,
    window: str | None = Query(None, description="Relative window, e.g. 24h, 7d, 4w."),
    from_raw: str | None = Query(None, alias="from", description="ISO-8601 lower bound."),
    to_raw: str | None = Query(None, alias="to", description="ISO-8601 upper bound."),
    end_customer_id: str | None = Query(None),
    token: Token = Depends(require_role("reader", "admin")),
    session: AsyncSession = Depends(session_dependency),
) -> dict[str, Any]:
    from_eff, to_eff, window_label, with_previous = _resolve_window(window, from_raw, to_raw)
    result = await SpanRepo(session).stats(
        end_customer_id=effective_scope(token, end_customer_id),  # admin may filter; reader pinned
        from_time=from_eff,
        to_time=to_eff,
        window_label=window_label,
        with_previous=with_previous,
    )
    await AccessLogger(session, request, token).log("read.stats", target_kind="stats", target_id="<aggregate>")
    await session.commit()
    return result
