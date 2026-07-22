"""Governance registry loading and join precedence."""

from __future__ import annotations

import json

import pytest
import yaml

from agent_capture_reporter.errors import TrajectoryLoadError
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceEntry, ModelGovernanceRegistry


def test_load_yaml_and_json(governance_registry_dict: dict, tmp_path) -> None:
    yaml_path = tmp_path / "reg.yaml"
    yaml_path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")
    json_path = tmp_path / "reg.json"
    json_path.write_text(json.dumps(governance_registry_dict), encoding="utf-8")

    from_yaml = ModelGovernanceRegistry.load(yaml_path)
    from_json = ModelGovernanceRegistry.load(json_path)
    assert len(from_yaml.entries) == 1
    assert from_json.entries[0].validation_status == "validated"


def test_load_top_level_list(tmp_path) -> None:
    path = tmp_path / "reg.json"
    path.write_text(json.dumps([{"model_card_version": "x", "intended_use": "y"}]), encoding="utf-8")
    reg = ModelGovernanceRegistry.load(path)
    assert reg.entries[0].model_card_version == "x"


def test_match_by_card_version_first(governance_registry_dict: dict, tmp_path) -> None:
    path = tmp_path / "reg.yaml"
    path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")
    reg = ModelGovernanceRegistry.load(path)
    entry = reg.match(
        provider="anthropic",
        model_name="claude-opus-4-7",
        model_version="2026-03-01",
        model_card_version="claude-opus-4-7.lending.v3",
    )
    assert entry is not None
    assert entry.risk_tier == "tier_1"


def test_match_identity_fallback_when_version_null() -> None:
    reg = ModelGovernanceRegistry(
        source="test",
        entries=[
            ModelGovernanceEntry(
                provider="anthropic", model_name="claude-opus-4-7", model_version=None, intended_use="any version"
            )
        ],
    )
    entry = reg.match(
        provider="anthropic", model_name="claude-opus-4-7", model_version="2026-03-01", model_card_version=None
    )
    assert entry is not None
    assert entry.intended_use == "any version"


def test_match_miss_returns_none(governance_registry_dict: dict, tmp_path) -> None:
    path = tmp_path / "reg.yaml"
    path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")
    reg = ModelGovernanceRegistry.load(path)
    assert reg.match(provider="openai", model_name="gpt-x", model_version="1", model_card_version="nope") is None


def test_load_missing_file(tmp_path) -> None:
    with pytest.raises(TrajectoryLoadError, match="does not exist"):
        ModelGovernanceRegistry.load(tmp_path / "absent.yaml")
