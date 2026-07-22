"""Extraction: section mapping, reason taxonomy, and gap classification."""

from __future__ import annotations

import pytest
from agent_capture.schema import Span, SpanType

from agent_capture_reporter.ecoa.extract import extract_adverse_action
from agent_capture_reporter.errors import IncompleteTrajectoryError
from agent_capture_reporter.trajectory import Trajectory


def _extract(spans: list[Span]):
    return extract_adverse_action(Trajectory.from_spans(spans))


def test_full_trajectory_extracts_all_sections(loan_denial_spans: list[Span]) -> None:
    result = _extract(loan_denial_spans)
    model = result.model

    assert model.decision == "deny"
    assert model.action_statement == "Your application for credit was denied."
    assert [r.code for r in model.principal_reasons] == ["high_dti", "delinquencies"]
    assert all(r.known for r in model.principal_reasons)
    assert model.credit_reporting_agencies[0].source_identifier == "experian.consumer-disclosure.v1"
    assert model.protected_class_check is not None
    assert model.human_review is not None
    assert model.human_review.approver_role == "senior_underwriter"
    assert model.notice_delivery is not None
    assert model.notice_delivery.success is True
    assert model.model_governance is not None
    assert model.model_governance.model_card_version == "claude-opus-4-7.lending.v3"

    assert result.gaps == []
    assert result.completeness_score == 1.0


def test_section_provenance_links_content_hashes(loan_denial_spans: list[Span]) -> None:
    result = _extract(loan_denial_spans)
    by_id = {s.span_id: s for s in loan_denial_spans}
    reasons = next(s for s in result.sections if s.section_id == "principal_reasons")
    assert reasons.status == "rendered"
    assert reasons.source_span_ids == ["4444444444444444"]
    assert reasons.source_content_hashes == [by_id["4444444444444444"].provenance.content_hash]
    assert reasons.rendered_value_digest is not None


def test_missing_model_call_raises_required(loan_denial_spans: list[Span]) -> None:
    spans = [s for s in loan_denial_spans if s.type is not SpanType.MODEL_CALL]
    with pytest.raises(IncompleteTrajectoryError) as exc:
        _extract(spans)
    assert "principal_reasons" in exc.value.missing


def test_missing_side_effect_is_expected_gap(loan_denial_spans: list[Span]) -> None:
    spans = [s for s in loan_denial_spans if s.type is not SpanType.SIDE_EFFECT]
    result = _extract(spans)
    assert result.model.notice_delivery is None
    gap = next(g for g in result.gaps if g.scope == "notice_delivery")
    assert gap.severity == "expected"
    assert result.completeness_score < 1.0


def test_missing_retrieval_is_expected_gap(loan_denial_spans: list[Span]) -> None:
    spans = [s for s in loan_denial_spans if s.type is not SpanType.RETRIEVAL]
    result = _extract(spans)
    assert result.model.credit_reporting_agencies == []
    assert any(g.scope == "credit_reporting_agency" and g.severity == "expected" for g in result.gaps)


def test_unknown_reason_code_renders_verbatim_and_flags_gap(loan_denial_spans: list[Span]) -> None:
    mc = next(s for s in loan_denial_spans if s.type is SpanType.MODEL_CALL)
    mutated = mc.model_copy(
        update={"outputs": {"recommendation": "deny", "primary_reason": "vibes_were_off", "secondary_reasons": []}}
    )
    spans = [s if s.type is not SpanType.MODEL_CALL else mutated for s in loan_denial_spans]
    # The mutation invalidates the span's recorded hash; this test exercises
    # extraction, not load-time tamper detection, so skip hash verification.
    result = extract_adverse_action(Trajectory.from_spans(spans, verify_hashes=False))
    reason = result.model.principal_reasons[0]
    assert reason.code == "vibes_were_off"
    assert reason.known is False
    assert any("vibes_were_off" in g.reason for g in result.gaps)
