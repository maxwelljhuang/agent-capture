"""Typed repository for the ledger.

Thin layer over SQLAlchemy that keeps SQL out of route handlers and gives
the routes a stable API to call. Methods that touch ``spans`` only INSERT
and SELECT — UPDATE/DELETE belong to the retention worker (different role).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_capture.schema import Span as ACSpan
from sqlalchemy import Text, and_, cast, distinct, func, literal, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.controls import Control, ControlCondition, current_catalog
from agent_capture_ledger.storage import models

# ---- DTOs --------------------------------------------------------------


@dataclass(frozen=True)
class StoredSpan:
    span_id: str
    trajectory_id: str
    parent_span_id: str | None
    start_time: datetime
    end_time: datetime
    content_hash: str
    parent_content_hash: str | None
    schema_version: str
    type: str
    status: str
    end_customer_id: str
    body: dict[str, object]


@dataclass(frozen=True)
class TokenRecord:
    token_id: str
    role: str
    end_customer_id: str | None
    revoked: bool
    expires_at: datetime | None


# ---- SpanRepo ----------------------------------------------------------


class SpanRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def fetch(self, span_id: str, trajectory_id: str) -> StoredSpan | None:
        stmt = select(models.Span).where(
            models.Span.span_id == span_id,
            models.Span.trajectory_id == trajectory_id,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return _to_stored(row)

    async def fetch_trajectory(self, trajectory_id: str) -> list[StoredSpan]:
        stmt = (
            select(models.Span)
            .where(models.Span.trajectory_id == trajectory_id)
            .order_by(models.Span.start_time, models.Span.span_id)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [_to_stored(r) for r in rows]

    async def list_trajectories(
        self,
        *,
        end_customer_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        regime: str | None,
        type_: str | None,
        status: str | None,
        agent_version: str | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> list[tuple[str, datetime, datetime, int]]:
        """Return (trajectory_id, first_start, last_end, span_count), paged."""
        # Aggregate by trajectory; filter spans on the way in.
        first_start = func.min(models.Span.start_time)
        last_end = func.max(models.Span.end_time)
        count = func.count(models.Span.span_id)

        stmt = select(
            models.Span.trajectory_id,
            first_start.label("first_start"),
            last_end.label("last_end"),
            count.label("n"),
        )
        conditions = []
        if end_customer_id is not None:
            conditions.append(models.Span.end_customer_id == end_customer_id)
        if from_time is not None:
            conditions.append(models.Span.start_time >= from_time)
        if to_time is not None:
            conditions.append(models.Span.start_time <= to_time)
        if regime is not None:
            conditions.append(models.Span.regulatory_regime.any(regime))
        if type_ is not None:
            conditions.append(models.Span.type == type_)
        if status is not None:
            conditions.append(models.Span.status == status)
        if agent_version is not None:
            conditions.append(models.Span.agent_version == agent_version)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.group_by(models.Span.trajectory_id)
        if cursor is not None:
            # paginate on (first_start DESC, trajectory_id DESC)
            from sqlalchemy import literal

            cursor_time, cursor_id = cursor
            stmt = stmt.having(
                tuple_(first_start, models.Span.trajectory_id) < tuple_(literal(cursor_time), literal(cursor_id))
            )
        stmt = stmt.order_by(first_start.desc(), models.Span.trajectory_id.desc()).limit(limit)
        rows = (await self._s.execute(stmt)).all()
        return [(r.trajectory_id, r.first_start, r.last_end, r.n) for r in rows]

    async def enrich_trajectories(self, trajectory_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Per-trajectory ``disposition``, aggregated ``regulatory_regime``, ``subject_ref``.

        Bounded by the caller's page (≤500 ids). ``disposition`` follows the
        plan rule: ``violation`` if any ``policy_check`` result is ``fail``;
        ``warn`` if any ``warn`` and no ``fail``; else ``clean``. ``subject_ref``
        is the root span's ``subject_id`` **only when it's a fingerprint**
        (``[FP:…]``) — guaranteed non-PII; otherwise ``None``.
        """
        ids = list(trajectory_ids)
        if not ids:
            return {}

        is_pc = models.Span.type == "policy_check"
        result_txt = models.Span.body["attributes"]["result"].astext
        disp_stmt = (
            select(
                models.Span.trajectory_id,
                func.bool_or(and_(is_pc, result_txt == "fail")).label("has_fail"),
                func.bool_or(and_(is_pc, result_txt == "warn")).label("has_warn"),
            )
            .where(models.Span.trajectory_id.in_(ids))
            .group_by(models.Span.trajectory_id)
        )

        regime_sub = (
            select(
                models.Span.trajectory_id.label("tid"),
                func.unnest(models.Span.regulatory_regime).label("regime"),
            )
            .where(models.Span.trajectory_id.in_(ids))
            .subquery()
        )
        regime_stmt = select(regime_sub.c.tid, func.array_agg(distinct(regime_sub.c.regime))).group_by(regime_sub.c.tid)

        # Root span's subject_id (parent_span_id is NULL) per trajectory.
        subj_stmt = select(
            models.Span.trajectory_id,
            models.Span.body["compliance"]["subject_id"].astext,
        ).where(models.Span.trajectory_id.in_(ids), models.Span.parent_span_id.is_(None))

        out: dict[str, dict[str, Any]] = {
            tid: {"disposition": "clean", "regulatory_regime": [], "subject_ref": None} for tid in ids
        }
        for tid, has_fail, has_warn in (await self._s.execute(disp_stmt)).all():
            out[tid]["disposition"] = "violation" if has_fail else ("warn" if has_warn else "clean")
        for tid, regimes in (await self._s.execute(regime_stmt)).all():
            out[tid]["regulatory_regime"] = sorted(regimes)
        for tid, subject_id in (await self._s.execute(subj_stmt)).all():
            # Only surface a fingerprinted subject_id — never a possibly-PII raw value.
            if subject_id and subject_id.startswith("[FP:"):
                out[tid]["subject_ref"] = subject_id
        return out

    async def list_access_log(
        self,
        *,
        end_customer_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> list[models.AccessLog]:
        """Read-only access-log rows (P5), tenant-scoped + cursor-paginated.

        Metadata only — who queried what, when. Ordered newest-first by
        ``(at, access_id)``.
        """
        aid_text = cast(models.AccessLog.access_id, Text)
        stmt = select(models.AccessLog)
        conditions = []
        if end_customer_id is not None:
            conditions.append(models.AccessLog.end_customer_id == end_customer_id)
        if from_time is not None:
            conditions.append(models.AccessLog.at >= from_time)
        if to_time is not None:
            conditions.append(models.AccessLog.at <= to_time)
        if cursor is not None:
            cursor_time, cursor_id = cursor
            conditions.append(tuple_(models.AccessLog.at, aid_text) < tuple_(literal(cursor_time), literal(cursor_id)))
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(models.AccessLog.at.desc(), aid_text.desc()).limit(limit)
        return list((await self._s.execute(stmt)).scalars().all())

    def _window_base(
        self,
        end_customer_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> list[Any]:
        """Condition list scoping the dashboard aggregates to a window (P7).

        A trajectory is "in window" when its ``first_start`` (min span start_time)
        falls in ``[from_time, to_time)`` — windowing on *when the trajectory
        began*, not on individual span times. With no bounds, only tenant scope
        applies (all-time, the v1.1 behavior).
        """
        tenant: list[Any] = []
        if end_customer_id is not None:
            tenant.append(models.Span.end_customer_id == end_customer_id)
        if from_time is None and to_time is None:
            return tenant

        first_start = func.min(models.Span.start_time)
        having: list[Any] = []
        if from_time is not None:
            having.append(first_start >= from_time)
        if to_time is not None:
            having.append(first_start < to_time)  # half-open so adjacent windows don't overlap
        in_window = select(models.Span.trajectory_id)
        if tenant:
            in_window = in_window.where(and_(*tenant))
        in_window = in_window.group_by(models.Span.trajectory_id).having(and_(*having))
        return [*tenant, models.Span.trajectory_id.in_(in_window)]

    async def _dashboard_aggregates(self, *, base: list[Any], include_controls: bool) -> dict[str, Any]:
        """Push-down dashboard aggregates (P0/P7) over the trajectories in ``base``.

        Trajectory volume, by-disposition counts, violation_count, and
        coverage_by_regime. ``controls`` (catalog-driven) only for the current
        window — the ``previous`` period passes ``include_controls=False``.
        Disposition vocabulary is clean | warn | violation.
        """
        where = and_(*base) if base else None

        is_pc = models.Span.type == "policy_check"
        result_txt = models.Span.body["attributes"]["result"].astext
        per_traj = select(
            models.Span.trajectory_id.label("tid"),
            func.bool_or(and_(is_pc, result_txt == "fail")).label("has_fail"),
            func.bool_or(and_(is_pc, result_txt == "warn")).label("has_warn"),
        )
        if where is not None:
            per_traj = per_traj.where(where)
        sub = per_traj.group_by(models.Span.trajectory_id).subquery()
        disp_stmt = select(
            func.count().label("total"),
            func.count().filter(sub.c.has_fail).label("violation"),
            func.count().filter(and_(sub.c.has_warn, ~sub.c.has_fail)).label("warn"),
            func.count().filter(and_(~sub.c.has_warn, ~sub.c.has_fail)).label("clean"),
        )
        d = (await self._s.execute(disp_stmt)).one()
        violation, warn, clean, total = int(d.violation), int(d.warn), int(d.clean), int(d.total)

        regime_sub = select(
            models.Span.trajectory_id.label("tid"),
            func.unnest(models.Span.regulatory_regime).label("regime"),
        )
        if where is not None:
            regime_sub = regime_sub.where(where)
        rs = regime_sub.subquery()
        cov_stmt = select(rs.c.regime, func.count(distinct(rs.c.tid))).group_by(rs.c.regime)
        coverage = {str(r[0]): int(r[1]) for r in (await self._s.execute(cov_stmt)).all()}

        controls = await self.evaluate_controls(current_catalog(), base=base) if include_controls else []

        return {
            "trajectory_volume": total,
            "by_disposition": {"clean": clean, "warn": warn, "violation": violation},
            "violation_count": violation,
            "coverage_by_regime": coverage,
            "controls": controls,
        }

    async def evaluate_controls(self, catalog: Sequence[Control], *, base: list[Any]) -> list[dict[str, Any]]:
        """Compute ``passing/total/status`` per control over the scoped window.

        ``base`` is the same tenant/window filter list used by the aggregates.
        ``status`` is ``pass`` when ``passing == total`` (incl. 0==0), else
        ``attention``. ``last_evaluated`` is the freshest span end_time in window.
        """
        if not catalog:
            return []
        le_stmt = select(func.max(models.Span.end_time))
        if base:
            le_stmt = le_stmt.where(and_(*base))
        data_through = (await self._s.execute(le_stmt)).scalar_one_or_none()
        last_evaluated = data_through.isoformat() if data_through is not None else None

        out: list[dict[str, Any]] = []
        for c in catalog:
            total = await self._count_trajectories([c.scope], base)
            passing = await self._count_trajectories([c.scope, c.pass_when], base)
            out.append(
                {
                    "regime": c.regime,
                    "key": c.key,
                    "label": c.label,
                    "passing": passing,
                    "total": total,
                    "last_evaluated": last_evaluated,
                    "status": "pass" if passing == total else "attention",
                }
            )
        return out

    async def _count_trajectories(self, conditions: Sequence[ControlCondition], base: list[Any]) -> int:
        """Distinct trajectories that satisfy EVERY condition (each via a span)."""
        wheres = list(base)
        for cond in conditions:
            sub_conds = list(base)
            if cond.regime is not None:
                sub_conds.append(models.Span.regulatory_regime.any(cond.regime))
            if cond.has_span_type is not None:
                sub_conds.append(models.Span.type == cond.has_span_type)
            sub = select(models.Span.trajectory_id)
            if sub_conds:
                sub = sub.where(and_(*sub_conds))
            wheres.append(models.Span.trajectory_id.in_(sub))
        stmt = select(func.count(distinct(models.Span.trajectory_id)))
        if wheres:
            stmt = stmt.where(and_(*wheres))
        return int((await self._s.execute(stmt)).scalar_one())

    async def stats(
        self,
        *,
        end_customer_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        window_label: str | None = None,
        with_previous: bool = False,
    ) -> dict[str, Any]:
        """Aggregate counts over a window — totals + by status + by type + dashboard.

        Computed in-place via SQL ``GROUP BY``; tenant-scoped like the other
        reads. Only counts leave the ledger, never span bodies.

        When ``with_previous`` (a finite window is active), a ``previous`` object
        carries the count-style dashboard aggregates over the immediately-
        preceding equal-length window so the dashboard can derive trend deltas
        (P7). Omitting the window preserves the v1.1 all-time response exactly.
        """
        # Basic counts keep their v1.1 span-start-time windowing.
        conditions = []
        if end_customer_id is not None:
            conditions.append(models.Span.end_customer_id == end_customer_id)
        if from_time is not None:
            conditions.append(models.Span.start_time >= from_time)
        if to_time is not None:
            conditions.append(models.Span.start_time <= to_time)

        totals_stmt = select(
            func.count(models.Span.span_id),
            func.count(func.distinct(models.Span.trajectory_id)),
        )
        status_stmt = select(models.Span.status, func.count(models.Span.span_id)).group_by(models.Span.status)
        type_stmt = select(models.Span.type, func.count(models.Span.span_id)).group_by(models.Span.type)
        if conditions:
            cond = and_(*conditions)
            totals_stmt = totals_stmt.where(cond)
            status_stmt = status_stmt.where(cond)
            type_stmt = type_stmt.where(cond)

        totals = (await self._s.execute(totals_stmt)).one()
        by_status = (await self._s.execute(status_stmt)).all()
        by_type = (await self._s.execute(type_stmt)).all()

        # Dashboard aggregates window on trajectory first_start (P7).
        current_base = self._window_base(end_customer_id, from_time, to_time)
        dashboard = await self._dashboard_aggregates(base=current_base, include_controls=True)

        result: dict[str, Any] = {
            "span_count": int(totals[0]),
            "trajectory_count": int(totals[1]),
            "by_status": {str(r[0]): int(r[1]) for r in by_status},
            "by_type": {str(r[0]): int(r[1]) for r in by_type},
            **dashboard,
        }

        if with_previous and from_time is not None and to_time is not None:
            length = to_time - from_time
            prev_base = self._window_base(end_customer_id, from_time - length, from_time)
            prev = await self._dashboard_aggregates(base=prev_base, include_controls=False)
            result["window"] = window_label
            result["previous"] = {
                "trajectory_volume": prev["trajectory_volume"],
                "violation_count": prev["violation_count"],
                "by_disposition": prev["by_disposition"],
                "coverage_by_regime": prev["coverage_by_regime"],
            }

        return result

    async def bulk_insert(
        self,
        spans: Iterable[ACSpan],
        *,
        batch_id: uuid.UUID,
    ) -> int:
        rows = [_to_row(s, batch_id=batch_id) for s in spans]
        if not rows:
            return 0
        # ON CONFLICT DO NOTHING: an idempotency check happened before us,
        # but a concurrent ingest of the same span from a duplicate retry
        # batch is still possible. Two workers both seeing "absent" then
        # both inserting → unique constraint fires; ignore it.
        stmt = (
            pg_insert(models.Span)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[models.Span.trajectory_id, models.Span.span_id, models.Span.start_time]
            )
        )
        result = await self._s.execute(stmt)
        # CursorResult exposes rowcount; the typing stubs widen the return
        # of execute() to Result, so we read it dynamically.
        rowcount = getattr(result, "rowcount", 0)
        return int(rowcount) if rowcount else 0


def _to_stored(row: models.Span) -> StoredSpan:
    return StoredSpan(
        span_id=row.span_id,
        trajectory_id=row.trajectory_id,
        parent_span_id=row.parent_span_id,
        start_time=row.start_time,
        end_time=row.end_time,
        content_hash=row.content_hash,
        parent_content_hash=row.parent_content_hash,
        schema_version=row.schema_version,
        type=row.type,
        status=row.status,
        end_customer_id=row.end_customer_id,
        body=row.body,
    )


def _to_row(span: ACSpan, *, batch_id: uuid.UUID) -> dict[str, object]:
    body_str = span.model_dump_json(exclude_none=False)
    body = json.loads(body_str)
    return {
        "span_id": span.span_id,
        "trajectory_id": span.trajectory_id,
        "parent_span_id": span.parent_span_id,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "end_customer_id": span.compliance.end_customer_id,
        "retention_class": span.compliance.retention_class.value,
        "regulatory_regime": [r.value for r in span.compliance.regulatory_regime],
        "data_classification": span.compliance.data_classification.value,
        "type": span.type.value,
        "status": span.status.value,
        "agent_version": span.compliance.agent_version,
        "policy_version_active": span.compliance.policy_version_active,
        "content_hash": span.provenance.content_hash,
        "parent_content_hash": span.provenance.parent_content_hash,
        "schema_version": span.provenance.schema_version,
        "body": body,
        "ingest_batch_id": batch_id,
    }


# ---- BatchRepo ---------------------------------------------------------


class BatchRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        batch_id: uuid.UUID,
        source_token_id: str,
        end_customer_id: str,
        span_count: int,
        accepted: int,
        rejected: int,
        duration_ms: int,
    ) -> None:
        self._s.add(
            models.IngestBatch(
                batch_id=batch_id,
                source_token_id=source_token_id,
                end_customer_id=end_customer_id,
                span_count=span_count,
                accepted=accepted,
                rejected=rejected,
                duration_ms=duration_ms,
            )
        )


# ---- QuarantineRepo ----------------------------------------------------


class QuarantineRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def write(
        self,
        *,
        raw_body: dict[str, Any] | Any,
        reason_code: str,
        reason_detail: str | None,
        end_customer_id: str | None,
        source_token_id: str | None,
        batch_id: uuid.UUID | None,
    ) -> None:
        # Pydantic models / dicts both ok; JSON via SQLAlchemy.
        if isinstance(raw_body, dict):
            body: dict[str, Any] = raw_body
        elif hasattr(raw_body, "model_dump"):
            body = raw_body.model_dump(mode="json")
        else:
            body = {"value": raw_body}
        self._s.add(
            models.Quarantine(
                quarantine_id=uuid.uuid4(),
                raw_body=body,
                reason_code=reason_code,
                reason_detail=reason_detail,
                end_customer_id=end_customer_id,
                source_token_id=source_token_id,
                batch_id=batch_id,
            )
        )


# ---- TokenRepo ---------------------------------------------------------


class TokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lookup(self, token_id: str) -> tuple[TokenRecord, str] | None:
        """Return (token record, stored Argon2 hash) for verification."""
        stmt = select(models.ApiToken).where(models.ApiToken.token_id == token_id)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return TokenRecord(
            token_id=row.token_id,
            role=row.role,
            end_customer_id=row.end_customer_id,
            revoked=row.revoked_at is not None,
            expires_at=row.expires_at,
        ), row.token_hash

    async def create(
        self,
        *,
        token_id: str,
        token_hash: str,
        role: str,
        end_customer_id: str | None,
        label: str | None,
        created_by: str | None,
    ) -> None:
        self._s.add(
            models.ApiToken(
                token_id=token_id,
                token_hash=token_hash,
                role=role,
                end_customer_id=end_customer_id,
                label=label,
                created_by=created_by,
            )
        )

    async def revoke(self, token_id: str) -> bool:
        stmt = select(models.ApiToken).where(models.ApiToken.token_id == token_id)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return False
        from datetime import UTC

        row.revoked_at = datetime.now(UTC)
        return True

    async def list_all(self) -> Sequence[TokenRecord]:
        stmt = select(models.ApiToken).order_by(models.ApiToken.created_at.desc())
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            TokenRecord(
                token_id=r.token_id,
                role=r.role,
                end_customer_id=r.end_customer_id,
                revoked=r.revoked_at is not None,
                expires_at=r.expires_at,
            )
            for r in rows
        ]
