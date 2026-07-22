"""Control catalog model + loader (no DB)."""

from __future__ import annotations

import pytest

from agent_capture_ledger.controls import (
    DEFAULT_CATALOG,
    ControlCondition,
    load_catalog,
    parse_catalog,
)


def test_default_catalog_keys() -> None:
    assert {c.key for c in DEFAULT_CATALOG} == {
        "adverse_action",
        "consumer_report",
        "model_rationale",
        "human_review",
    }


def test_parse_catalog_roundtrip() -> None:
    cat = parse_catalog(
        {
            "controls": [
                {
                    "regime": "ECOA",
                    "key": "k",
                    "label": "L",
                    "scope": {"regime": "ECOA"},
                    "pass_when": {"has_span_type": "policy_check"},
                }
            ]
        }
    )
    assert cat[0].key == "k"
    assert cat[0].scope.regime == "ECOA"
    assert cat[0].pass_when.has_span_type == "policy_check"


def test_condition_requires_a_field() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ControlCondition()


def test_parse_rejects_missing_required_field() -> None:
    with pytest.raises(ValueError, match="missing required field"):
        parse_catalog({"controls": [{"key": "k"}]})


def test_load_catalog_from_yaml(tmp_path) -> None:
    f = tmp_path / "controls.yaml"
    f.write_text(
        "controls:\n"
        "  - regime: FCRA\n    key: cra\n    label: CRA\n"
        "    scope: { regime: FCRA }\n    pass_when: { has_span_type: tool_call }\n",
        encoding="utf-8",
    )
    cat = load_catalog(f)
    assert cat[0].key == "cra"
    assert cat[0].pass_when.has_span_type == "tool_call"
