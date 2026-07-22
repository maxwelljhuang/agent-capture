"""Monthly partition management for ``spans``.

The migration ships the current month and next month. A small helper here
ensures a partition exists for an arbitrary date — called by:
- the retention worker (when probing ``DROP TABLE`` candidates)
- a cron that runs ``ledger db partitions ensure --month +1``

Uses a separate AUTOCOMMIT connection because ``CREATE TABLE`` inside a
transaction has weird interactions with the partition parent's metadata
locks on long-running readers.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def partition_name(year: int, month: int) -> str:
    return f"spans_{year:04d}_{month:02d}"


def _bounds(year: int, month: int) -> tuple[str, str]:
    nxt_year, nxt_month = (year, month + 1) if month < 12 else (year + 1, 1)
    return (f"{year:04d}-{month:02d}-01", f"{nxt_year:04d}-{nxt_month:02d}-01")


async def ensure_partition(conn: AsyncConnection, year: int, month: int) -> None:
    name = partition_name(year, month)
    lo, hi = _bounds(year, month)
    await conn.execute(
        text(f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF spans FOR VALUES FROM ('{lo}') TO ('{hi}')")
    )


async def drop_partition(conn: AsyncConnection, name: str) -> None:
    await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


async def list_partitions(conn: AsyncConnection) -> list[str]:
    """Return names of all ``spans_YYYY_MM`` partitions, in date order."""
    rows = (
        await conn.execute(
            text(
                "SELECT inhrelid::regclass::text AS name "
                "FROM pg_inherits "
                "WHERE inhparent = 'spans'::regclass "
                "ORDER BY name"
            )
        )
    ).all()
    return [r.name for r in rows]


def month_offset(d: datetime, offset_months: int) -> tuple[int, int]:
    """Return (year, month) for ``d`` shifted by ``offset_months``."""
    total = d.year * 12 + (d.month - 1) + offset_months
    return total // 12, (total % 12) + 1
