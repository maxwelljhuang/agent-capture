"""SR 11-7 extraction: grouping, governance join, evidence digest, gap posture."""

from __future__ import annotations

import hashlib

import pytest
from agent_capture.schema import Span

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod
from agent_capture_reporter.errors import IncompleteInventoryError
from agent_capture_reporter.sr_11_7.extract import extract_model_inventory
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceRegistry
from agent_capture_reporter.trajectory import Trajectory

PERIOD = ReportingPeriod.parse("2026-01-01:2026-03-31")


def _corpus(trajectories: list[list[Span]]) -> Corpus:
    return Corpus(trajectories=tuple(Trajectory.from_spans(t) for t in trajectories))


def _registry(d: dict) -> ModelGovernanceRegistry:
    return ModelGovernanceRegistry(source="test-registry", entries=d["models"])  # type: ignore[arg-type]


def test_groups_into_one_row_per_model_version(inventory_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    keys = {e.model_key for e in result.model.entries}
    assert keys == {
        "anthropic|claude-opus-4-7|2026-03-01",
        "anthropic|claude-opus-4-7|2026-02-01",
        "anthropic|claude-sonnet-4-6|2026-01-10",
    }
    assert result.model.total_models == 3


def test_governed_model_pulls_registry_columns(inventory_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    a = next(e for e in result.model.entries if e.model_version == "2026-03-01")
    assert a.governed is True
    assert a.governance.risk_tier == "tier_1"
    assert a.governance.validation_status == "validated"
    # Two trajectories, tokens summed.
    assert a.usage.decision_count == 2
    assert a.usage.total_tokens == 2200
    assert result.model.governed_models == 1


def test_ungoverned_model_is_finding_not_failure(inventory_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    b = next(e for e in result.model.entries if e.model_version == "2026-02-01")
    assert b.governed is False
    assert b.governance.validation_status == "NO GOVERNANCE CARD"
    assert "anthropic|claude-opus-4-7|2026-02-01" in result.models_missing_card
    # Required-severity gap recorded, but extraction did NOT raise.
    assert any(g.scope == b.model_key and g.severity == "required" for g in result.gaps)


def test_card_not_in_registry_is_expected_gap(inventory_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    c = next(e for e in result.model.entries if e.model_name == "claude-sonnet-4-6")
    assert c.governance.validation_status == "[NOT IN REGISTRY]"
    assert "anthropic|claude-sonnet-4-6|2026-01-10" in result.models_missing_registry_entry
    assert c.usage.total_tokens is None  # tokens unavailable for this model


def test_evidence_digest_recomputable(inventory_corpus, governance_registry_dict) -> None:
    corpus = _corpus(inventory_corpus)
    result = extract_model_inventory(corpus, _registry(governance_registry_dict), PERIOD)
    prov = next(p for p in result.entries_provenance if p.model_key == "anthropic|claude-opus-4-7|2026-03-01")
    # Independently recompute over the two contributing model_call spans.
    hashes = sorted(
        s.provenance.content_hash
        for traj in corpus
        for s in traj.spans
        if s.type.value == "model_call" and s.attributes.model_version == "2026-03-01"  # type: ignore[union-attr]
    )
    expected = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
    assert prov.evidence_digest == expected
    assert prov.contributing_span_count == 2


def test_unvalidated_rollup(inventory_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    # Only model A is validated; B (no card) and C (not in registry) are not.
    assert set(result.model.unvalidated_models) == {
        "anthropic|claude-opus-4-7|2026-02-01",
        "anthropic|claude-sonnet-4-6|2026-01-10",
    }


def test_empty_period_raises(inventory_corpus, governance_registry_dict) -> None:
    empty_period = ReportingPeriod.parse("2020-01-01:2020-12-31")
    with pytest.raises(IncompleteInventoryError) as exc:
        extract_model_inventory(_corpus(inventory_corpus), _registry(governance_registry_dict), empty_period)
    assert exc.value.reason_code == "no_model_usage"


def test_ambiguous_tenant_raises(multi_tenant_corpus, governance_registry_dict) -> None:
    with pytest.raises(IncompleteInventoryError) as exc:
        extract_model_inventory(_corpus(multi_tenant_corpus), _registry(governance_registry_dict), PERIOD)
    assert exc.value.reason_code == "ambiguous_tenant"


def test_tenant_filter_scopes_inventory(multi_tenant_corpus, governance_registry_dict) -> None:
    result = extract_model_inventory(
        _corpus(multi_tenant_corpus), _registry(governance_registry_dict), PERIOD, tenant="acme-bank"
    )
    assert result.model.total_models == 1
    assert result.model.entries[0].usage.decision_count == 1
