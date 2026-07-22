"""Partition name + month arithmetic."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_capture_ledger.storage.partitions import month_offset, partition_name


def test_partition_name_pads() -> None:
    assert partition_name(2026, 5) == "spans_2026_05"
    assert partition_name(2026, 12) == "spans_2026_12"


def test_month_offset_forwards() -> None:
    d = datetime(2026, 5, 15, tzinfo=UTC)
    assert month_offset(d, 0) == (2026, 5)
    assert month_offset(d, 1) == (2026, 6)
    assert month_offset(d, 7) == (2026, 12)
    assert month_offset(d, 8) == (2027, 1)


def test_month_offset_backwards() -> None:
    d = datetime(2026, 2, 1, tzinfo=UTC)
    assert month_offset(d, -1) == (2026, 1)
    assert month_offset(d, -2) == (2025, 12)
