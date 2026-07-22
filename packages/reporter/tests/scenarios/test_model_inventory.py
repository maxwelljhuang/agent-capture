"""End-to-end render of an SR 11-7 Model Inventory from a corpus + registry."""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from agent_capture.schema import Span

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod
from agent_capture_reporter.render.pdf import pdf_available
from agent_capture_reporter.report import render_model_inventory
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceRegistry
from agent_capture_reporter.trajectory import Trajectory

PERIOD = ReportingPeriod.parse("2026-01-01:2026-03-31")


def _corpus(trajectories: list[list[Span]]) -> Corpus:
    return Corpus(trajectories=tuple(Trajectory.from_spans(t) for t in trajectories))


def _registry(d: dict) -> ModelGovernanceRegistry:
    return ModelGovernanceRegistry(source="reg.yaml", entries=d["models"])  # type: ignore[arg-type]


def test_renders_inventory_html(inventory_corpus, governance_registry_dict, generated_at: datetime) -> None:
    rendered = render_model_inventory(
        _corpus(inventory_corpus),
        _registry(governance_registry_dict),
        PERIOD,
        generated_at=generated_at,
        with_pdf=False,
    )
    flat = " ".join(rendered.html.split())

    # Required columns / content present.
    assert "Model Inventory" in flat
    assert "claude-opus-4-7" in flat
    assert "Unsecured personal loan underwriting risk score." in flat  # registry intended_use
    assert "tier_1" in flat
    assert "validated" in flat
    # Ungoverned model surfaced as a finding, not hidden.
    assert "NO GOVERNANCE CARD" in flat
    assert "[NOT IN REGISTRY]" in flat
    # The out-of-chain disclosure note.
    assert "outside" in flat
    assert "hash chain" in flat


def test_inventory_manifest_contract(inventory_corpus, governance_registry_dict, generated_at: datetime) -> None:
    rendered = render_model_inventory(
        _corpus(inventory_corpus),
        _registry(governance_registry_dict),
        PERIOD,
        generated_at=generated_at,
        with_pdf=False,
    )
    m = rendered.manifest
    assert m.notice_type == "sr_11_7_model_inventory"
    assert m.total_models == 3
    assert m.governed_models == 1
    assert m.trajectories_scanned == 4
    assert m.hash_chain_verified is True
    assert "anthropic|claude-opus-4-7|2026-02-01" in m.models_missing_card
    assert "anthropic|claude-sonnet-4-6|2026-01-10" in m.models_missing_registry_entry
    assert m.html_sha256 == hashlib.sha256(rendered.html.encode("utf-8")).hexdigest()

    # Governance cells are labelled as out-of-chain; span cells are not.
    a = next(e for e in m.entries if e.model_key == "anthropic|claude-opus-4-7|2026-03-01")
    kinds = {c.column_id: c.provenance_kind for c in a.columns}
    assert kinds["intended_use"] == "governance_registry"
    assert kinds["identity"] == "span_corpus"
    assert kinds["usage"] == "computed"
    # The governed row cites its contributing trajectories + a recomputable digest.
    assert sorted(a.contributing_trajectory_ids) == ["a1" * 16, "a2" * 16]
    assert a.evidence_digest


def test_inventory_render_is_deterministic(inventory_corpus, governance_registry_dict, generated_at: datetime) -> None:
    args = (_corpus(inventory_corpus), _registry(governance_registry_dict), PERIOD)
    a = render_model_inventory(*args, generated_at=generated_at, with_pdf=False)
    b = render_model_inventory(*args, generated_at=generated_at, with_pdf=False)
    assert a.html == b.html
    assert a.manifest.model_dump_json() == b.manifest.model_dump_json()


@pytest.mark.pdf
def test_renders_inventory_pdf(inventory_corpus, governance_registry_dict, generated_at: datetime) -> None:
    if not pdf_available():
        pytest.skip("WeasyPrint not installed")
    rendered = render_model_inventory(
        _corpus(inventory_corpus),
        _registry(governance_registry_dict),
        PERIOD,
        generated_at=generated_at,
        with_pdf=True,
    )
    assert rendered.pdf is not None
    assert rendered.pdf[:5] == b"%PDF-"
    assert rendered.manifest.pdf_sha256 is not None
