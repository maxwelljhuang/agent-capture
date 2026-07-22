"""Strategy unit tests — FullRedaction, HmacFingerprint, PassThrough."""

from __future__ import annotations

import pytest

from agent_capture.errors import RedactionError
from agent_capture.redaction.strategies import (
    FullRedaction,
    HmacFingerprint,
    PassThrough,
)


def test_full_redaction_replaces_with_sentinel() -> None:
    assert FullRedaction().redact("123-45-6789", field_type="ssn") == "[REDACTED:ssn]"


def test_pass_through_returns_value_unchanged() -> None:
    assert PassThrough().redact("hello", field_type="text") == "hello"


def test_hmac_with_inline_key_is_deterministic() -> None:
    s = HmacFingerprint(key=b"k", truncate=16)
    a = s.redact("123-45-6789", field_type="ssn")
    b = s.redact("123-45-6789", field_type="ssn")
    assert a == b
    assert a.startswith("[FP:")
    assert a.endswith(":ssn]")


def test_hmac_different_values_produce_different_fingerprints() -> None:
    s = HmacFingerprint(key=b"k")
    assert s.redact("a", field_type="x") != s.redact("b", field_type="x")


def test_hmac_reads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_KEY", "secret")
    s = HmacFingerprint(key_env="TEST_KEY")
    out = s.redact("v", field_type="x")
    assert out.startswith("[FP:")


def test_hmac_missing_env_raises_redaction_error() -> None:
    s = HmacFingerprint(key_env="MISSING_HMAC_KEY_XYZ")
    with pytest.raises(RedactionError, match="env var"):
        s.redact("v", field_type="x")


def test_hmac_no_key_at_all_raises() -> None:
    s = HmacFingerprint()
    with pytest.raises(RedactionError, match="requires either"):
        s.redact("v", field_type="x")
