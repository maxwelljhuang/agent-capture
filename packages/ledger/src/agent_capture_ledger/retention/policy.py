"""RetentionClass → timedelta mapping driven by settings."""

from __future__ import annotations

from datetime import timedelta

from agent_capture.schema.compliance import RetentionClass

from agent_capture_ledger.config import get_settings


def ttl_for(klass: str | RetentionClass) -> timedelta | None:
    """Return the retention window for a class. ``None`` = never delete."""
    v = klass.value if isinstance(klass, RetentionClass) else klass
    s = get_settings()
    if v == RetentionClass.TRANSIENT.value:
        return timedelta(days=s.retention_transient_days)
    if v == RetentionClass.STANDARD.value:
        return timedelta(days=s.retention_standard_days)
    if v == RetentionClass.EXTENDED.value:
        return timedelta(days=s.retention_extended_days)
    if v == RetentionClass.LITIGATION_HOLD.value:
        return None
    return None
