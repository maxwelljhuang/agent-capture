"""Shared pytest fixtures for the agent-capture test suite."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def now() -> datetime:
    """A frozen, timezone-aware UTC datetime for deterministic tests."""
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock(now: datetime) -> Iterator[Iterator[datetime]]:
    """A monotonic clock advancing 1ms per call. Useful for ordered spans."""

    def _ticks() -> Iterator[datetime]:
        t = now
        while True:
            yield t
            t = t + timedelta(milliseconds=1)

    return _ticks()


@pytest.fixture
def span_id_factory() -> callable[[], str]:  # type: ignore[name-defined]
    """Generate fresh 16-hex span ids."""

    def _make() -> str:
        return secrets.token_hex(8)

    return _make


@pytest.fixture
def trajectory_id_factory() -> callable[[], str]:  # type: ignore[name-defined]
    """Generate fresh 32-hex trajectory (trace) ids."""

    def _make() -> str:
        return secrets.token_hex(16)

    return _make
