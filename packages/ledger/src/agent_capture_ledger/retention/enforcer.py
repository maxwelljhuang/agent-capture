"""Retention enforcement.

The worker uses the ``ledger_retention`` role — the only role whose
``DELETE`` statements pass the append-only trigger. Two paths:

1. **Drop-partition fast path** — a whole monthly partition where every
   span is past its TTL and no rows are under litigation hold. ``DROP
   TABLE`` is O(1).
2. **Row-delete slow path** — a partition with mixed classes (some spans
   still in-window) gets per-row ``DELETE`` for the expired rows.

Every operation writes a ``retention_operations`` row with the
class, kind, target, and count — the audit trail of the audit trail.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from agent_capture.schema.compliance import RetentionClass
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from agent_capture_ledger.observability import metrics
from agent_capture_ledger.retention.policy import ttl_for

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionReport:
    partitions_dropped: list[str]
    rows_deleted: dict[str, int]  # retention_class → rows


async def run_retention(retention_engine: AsyncEngine, *, now: datetime | None = None) -> RetentionReport:
    now = now or datetime.now(UTC)
    dropped: list[str] = []
    rows_deleted: dict[str, int] = {}

    async with retention_engine.connect() as conn:
        partitions = (
            await conn.execute(
                text(
                    "SELECT inhrelid::regclass::text AS name "
                    "FROM pg_inherits WHERE inhparent='spans'::regclass ORDER BY name"
                )
            )
        ).all()

        # ---- per-partition: see if we can drop the whole thing ----
        for row in partitions:
            name = row.name
            # find the partition bounds + max retention window present
            stmt = text(f"""
                SELECT
                    MAX(end_time) AS max_end,
                    MAX(retention_class) AS uniform_class,
                    COUNT(DISTINCT retention_class) AS class_count,
                    EXISTS (
                        SELECT 1 FROM litigation_holds h
                        WHERE h.released_at IS NULL
                          AND h.trajectory_id IN (
                              SELECT trajectory_id FROM {name}
                          )
                    ) AS held
                FROM {name}
            """)
            r = (await conn.execute(stmt)).one()
            if r.max_end is None or r.held:
                continue
            if r.class_count == 1:
                ttl = ttl_for(r.uniform_class)
                if ttl is not None and r.max_end + ttl < now:
                    await conn.execute(text(f"DROP TABLE {name}"))
                    await _record_op(
                        conn, kind="drop_partition", target=name, spans_affected=0, retention_class=r.uniform_class
                    )
                    dropped.append(name)
                    metrics.retention_deleted.labels(**{"class": r.uniform_class, "kind": "drop_partition"}).inc()
                    continue

            # ---- mixed-class partition: row-delete slow path ----
            for klass in (RetentionClass.TRANSIENT, RetentionClass.STANDARD, RetentionClass.EXTENDED):
                ttl = ttl_for(klass)
                if ttl is None:
                    continue
                cutoff = now - ttl
                # respect litigation hold
                delete_stmt = text(f"""
                    DELETE FROM {name} s
                    WHERE s.retention_class = :klass
                      AND s.end_time < :cutoff
                      AND NOT EXISTS (
                          SELECT 1 FROM litigation_holds h
                          WHERE h.released_at IS NULL
                            AND h.trajectory_id = s.trajectory_id
                      )
                """)
                result = await conn.execute(delete_stmt, {"klass": klass.value, "cutoff": cutoff})
                n = result.rowcount or 0
                if n > 0:
                    rows_deleted[klass.value] = rows_deleted.get(klass.value, 0) + n
                    await _record_op(
                        conn, kind="delete_rows", target=name, spans_affected=n, retention_class=klass.value
                    )
                    metrics.retention_deleted.labels(**{"class": klass.value, "kind": "delete_rows"}).inc(n)
        await conn.commit()

    return RetentionReport(partitions_dropped=dropped, rows_deleted=rows_deleted)


async def _record_op(
    conn: AsyncConnection, *, kind: str, target: str, spans_affected: int, retention_class: str
) -> None:
    await conn.execute(
        text("""
        INSERT INTO retention_operations
            (op_id, op_kind, target, spans_affected, retention_class, executed_by)
        VALUES (:op_id, :kind, :target, :n, :klass, :by)
    """),
        {
            "op_id": uuid.uuid4(),
            "kind": kind,
            "target": target,
            "n": spans_affected,
            "klass": retention_class,
            "by": "retention_worker",
        },
    )
