"""Canonical serialization tests.

The provenance hash chain depends on canonical_bytes producing deterministic
output. These tests are the contract between Python and TypeScript: any TS
implementation that produces the same logical span must emit byte-identical
canonical bytes.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from agent_capture.schema.canonical import (
    canonical_bytes,
    canonical_json,
    content_hash,
)


def test_keys_are_sorted() -> None:
    payload = {"z": 1, "a": 2, "m": {"y": 1, "x": 2}}
    assert canonical_json(payload) == '{"a":2,"m":{"x":2,"y":1},"z":1}'


def test_separators_are_minimal() -> None:
    assert canonical_json({"a": 1}) == '{"a":1}'


def test_datetimes_render_as_utc_with_microseconds() -> None:
    naive = datetime(2026, 5, 17, 12, 0, 0)
    aware_utc = naive.replace(tzinfo=UTC)
    payload = {"t": aware_utc}
    assert canonical_json(payload) == '{"t":"2026-05-17T12:00:00.000000Z"}'

    # Naive datetimes are coerced to UTC.
    assert canonical_json({"t": naive}) == canonical_json(payload)


def test_nan_and_infinity_are_rejected() -> None:
    import math

    import pytest

    with pytest.raises(ValueError, match="forbids NaN"):
        canonical_bytes({"x": math.nan})
    with pytest.raises(ValueError, match="forbids"):
        canonical_bytes({"x": math.inf})


def test_provenance_excluded_by_default() -> None:
    payload = {"span_id": "a" * 16, "provenance": {"content_hash": "ff" * 32}}
    out = canonical_json(payload)
    assert "provenance" not in out
    assert "span_id" in out


def test_provenance_included_when_requested() -> None:
    payload = {"x": 1, "provenance": {"content_hash": "ff" * 32}}
    out = canonical_json(payload, exclude_provenance=False)
    assert "provenance" in out


def test_content_hash_is_stable() -> None:
    payload = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    expected = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    assert content_hash(payload) == expected
    # Two structurally identical payloads produce the same hash regardless of dict order.
    reordered = {"c": {"nested": True}, "b": [1, 2, 3], "a": 1}
    assert content_hash(reordered) == expected
