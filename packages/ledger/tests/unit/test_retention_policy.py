"""Retention policy mapping."""

from __future__ import annotations

from datetime import timedelta

from agent_capture.schema.compliance import RetentionClass

from agent_capture_ledger.config import Settings, set_settings
from agent_capture_ledger.retention.policy import ttl_for


def setup_function() -> None:
    set_settings(
        Settings(
            retention_transient_days=1,
            retention_standard_days=30,
            retention_extended_days=365,
        )
    )


def test_transient_ttl() -> None:
    assert ttl_for(RetentionClass.TRANSIENT) == timedelta(days=1)


def test_standard_ttl() -> None:
    assert ttl_for(RetentionClass.STANDARD) == timedelta(days=30)


def test_extended_ttl() -> None:
    assert ttl_for(RetentionClass.EXTENDED) == timedelta(days=365)


def test_litigation_hold_is_indefinite() -> None:
    assert ttl_for(RetentionClass.LITIGATION_HOLD) is None


def test_accepts_str_values() -> None:
    assert ttl_for("transient") == timedelta(days=1)
