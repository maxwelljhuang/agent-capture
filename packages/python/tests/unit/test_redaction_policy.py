"""Policy load and lookup tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_capture.errors import RedactionError
from agent_capture.redaction.policy import (
    load_policy,
    parse_policy,
    pass_through_policy,
)
from agent_capture.redaction.strategies import (
    FullRedaction,
    HmacFingerprint,
    PassThrough,
)


def _doc() -> dict:
    return {
        "version": "v1",
        "default_strategy": "full",
        "strategies": {
            "full": {"type": "full"},
            "hmac": {"type": "hmac", "key": "k"},
            "public": {"type": "pass_through"},
        },
        "field_rules": [
            {"field_name": "ssn", "strategy": "full"},
            {"field_name": "account_number", "strategy": "hmac"},
        ],
        "pattern_rules": [
            {"field_type": "ssn", "strategy": "full"},
            {"field_type": "routing_number", "strategy": "hmac"},
        ],
    }


def test_parse_round_trip() -> None:
    p = parse_policy(_doc())
    assert p.version == "v1"
    assert isinstance(p.strategies["full"], FullRedaction)
    assert isinstance(p.strategies["hmac"], HmacFingerprint)
    assert isinstance(p.strategies["public"], PassThrough)


def test_load_policy_reads_yaml(tmp_path: Path) -> None:
    f = tmp_path / "policy.yaml"
    f.write_text(yaml.safe_dump(_doc()), encoding="utf-8")
    p = load_policy(f)
    assert p.version == "v1"


def test_field_rule_case_insensitive() -> None:
    p = parse_policy(_doc())
    assert isinstance(p.strategy_for_field("SSN"), FullRedaction)
    assert isinstance(p.strategy_for_field("Ssn"), FullRedaction)


def test_unknown_field_returns_none() -> None:
    p = parse_policy(_doc())
    assert p.strategy_for_field("not_a_field") is None


def test_pattern_lookup_falls_back_to_default() -> None:
    p = parse_policy(_doc())
    # 'unmapped_type' is not in pattern_rules → default_strategy (full) is used.
    assert isinstance(p.strategy_for_pattern("unmapped_type"), FullRedaction)


def test_missing_version_raises() -> None:
    doc = _doc()
    del doc["version"]
    with pytest.raises(RedactionError, match="version"):
        parse_policy(doc)


def test_unknown_strategy_type_raises() -> None:
    doc = _doc()
    doc["strategies"]["bogus"] = {"type": "nope"}
    with pytest.raises(RedactionError, match="unknown type"):
        parse_policy(doc)


def test_rule_referencing_undefined_strategy_raises() -> None:
    doc = _doc()
    doc["field_rules"].append({"field_name": "x", "strategy": "missing"})
    with pytest.raises(RedactionError, match="undefined strategy"):
        parse_policy(doc)


def test_redaction_floor_overrides_pass_through_for_pii() -> None:
    # Even a fully-permissive pass-through policy may not ship recognized PII in
    # cleartext: the floor coerces it to full redaction.
    p = pass_through_policy()
    for field_type in ("ssn", "routing_number", "account_number", "micr", "date_of_birth"):
        s = p.strategy_for_pattern(field_type)
        assert isinstance(s, FullRedaction), field_type
        assert s.redact("123-45-6789", field_type=field_type) == f"[REDACTED:{field_type}]"


def test_pass_through_still_applies_to_non_pii() -> None:
    # The floor is scoped to recognized PII; a non-PII field type still passes
    # through under a pass-through policy.
    p = pass_through_policy()
    s = p.strategy_for_pattern("public_marketing_id")
    assert isinstance(s, PassThrough)
    assert s.redact("anything", field_type="public_marketing_id") == "anything"


def test_redaction_floor_allows_hmac_for_pii() -> None:
    # The floor forbids cleartext, not HMAC — a policy may still fingerprint PII.
    doc = _doc()
    doc["default_strategy"] = "full"
    doc["pattern_rules"] = [{"field_type": "ssn", "strategy": "hmac"}]
    p = parse_policy(doc)
    assert isinstance(p.strategy_for_pattern("ssn"), HmacFingerprint)
