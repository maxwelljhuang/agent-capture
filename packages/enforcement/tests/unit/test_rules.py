"""Rule model + loader tests (mirror the redaction policy tests)."""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_capture.schema import SpanType

from agent_capture_enforcement.errors import RuleLoadError
from agent_capture_enforcement.rules import load_rules, parse_rules

_VALID = {
    "version": "enforcement-lending-v1.0.0",
    "rules": [
        {
            "id": "aa.allowlist",
            "span_type": "side_effect",
            "action_type": ["document.mail"],
            "evaluator": "action_type_allowed",
            "params": {"allow": ["document.mail"]},
            "mode": "blocking",
            "failure_mode": "fail_closed",
        },
        {
            "id": "approval.signed",
            "span_type": "human_approval",
            "evaluator": "require_attribute",
            "params": {"attribute": "signature"},
            "on_fail": "warn",
            "enabled": False,
        },
    ],
}


def test_parse_valid_ruleset() -> None:
    rs = parse_rules(_VALID)
    assert rs.version == "enforcement-lending-v1.0.0"
    assert len(rs.rules) == 2
    r0 = rs.rules[0]
    assert r0.id == "aa.allowlist"
    assert r0.span_type is SpanType.SIDE_EFFECT
    assert r0.action_type == ("document.mail",)
    assert r0.mode == "blocking"
    assert r0.failure_mode == "fail_closed"


def test_rules_for_filters_by_type_action_and_enabled() -> None:
    rs = parse_rules(_VALID)
    # human_approval rule is disabled → no match
    assert rs.rules_for(SpanType.HUMAN_APPROVAL) == []
    # side_effect with matching action_type
    assert [r.id for r in rs.rules_for(SpanType.SIDE_EFFECT, "document.mail")] == ["aa.allowlist"]
    # side_effect with non-matching action_type → filtered out
    assert rs.rules_for(SpanType.SIDE_EFFECT, "payment.transfer") == []


def test_version_required() -> None:
    with pytest.raises(RuleLoadError, match="version"):
        parse_rules({"rules": []})


def test_unknown_evaluator_rejected() -> None:
    with pytest.raises(RuleLoadError, match="unknown evaluator"):
        parse_rules({"version": "v1", "rules": [{"id": "r", "span_type": "side_effect", "evaluator": "nope"}]})


def test_invalid_span_type_rejected() -> None:
    with pytest.raises(RuleLoadError, match="span_type"):
        parse_rules({"version": "v1", "rules": [{"id": "r", "span_type": "banana", "evaluator": "always_pass"}]})


def test_duplicate_rule_id_rejected() -> None:
    doc = {
        "version": "v1",
        "rules": [
            {"id": "dup", "span_type": "side_effect", "evaluator": "always_pass"},
            {"id": "dup", "span_type": "side_effect", "evaluator": "always_pass"},
        ],
    }
    with pytest.raises(RuleLoadError, match="Duplicate rule id"):
        parse_rules(doc)


def test_invalid_enum_rejected() -> None:
    doc = {
        "version": "v1",
        "rules": [{"id": "r", "span_type": "side_effect", "evaluator": "always_pass", "failure_mode": "explode"}],
    }
    with pytest.raises(RuleLoadError, match="failure_mode"):
        parse_rules(doc)


def test_load_rules_from_file(tmp_path: Path) -> None:
    p = tmp_path / "rules.yaml"
    p.write_text(
        "version: v2\nrules:\n  - id: r\n    span_type: side_effect\n    evaluator: always_pass\n",
        encoding="utf-8",
    )
    rs = load_rules(p)
    assert rs.version == "v2"
    assert rs.rules[0].id == "r"


def test_load_rules_missing_file() -> None:
    with pytest.raises(RuleLoadError, match="Could not read"):
        load_rules("/nonexistent/rules.yaml")
