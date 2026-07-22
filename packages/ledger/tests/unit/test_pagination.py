"""Cursor encode/decode roundtrips."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_capture_ledger.api.pagination import Cursor


def test_roundtrip() -> None:
    c = Cursor(after_time=datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC), after_id="a" * 32)
    encoded = c.encode()
    decoded = Cursor.decode(encoded)
    assert decoded is not None
    assert decoded.after_time == c.after_time
    assert decoded.after_id == c.after_id


def test_decode_none_when_empty() -> None:
    assert Cursor.decode(None) is None
    assert Cursor.decode("") is None


def test_decode_returns_none_for_garbage() -> None:
    assert Cursor.decode("not-base64!!") is None
    assert Cursor.decode("YWJj") is None  # valid base64, wrong JSON shape
