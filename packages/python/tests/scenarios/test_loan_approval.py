"""Loan approval / denial scenario.

This is the canonical end-to-end test for the Week 6 deliverable: a trajectory
must contain every field the adverse-action report generator (in the reporting
layer) will eventually need. If this test ever needs a new field, that's a
schema change, not a test change.

Flow:
    root planner_step "underwrite_application"
    ├── retrieval "fetch_credit_report"
    ├── policy_check "ecoa_protected_class_check"
    ├── model_call "score_application"
    ├── planner_step "decision"
    ├── human_approval "underwriter_review"     [critical span]
    └── side_effect "send_adverse_action_letter" [critical span]
"""

from __future__ import annotations

from agent_capture.schema import (
    ComplianceMetadata,
    RegulatoryRegime,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
)

from ._helpers import (
    assert_reporting_fields_populated,
    assert_trajectory_well_formed,
    build_span,
    utc,
)


def _make_compliance(*, prompt_version: str | None = None, model_card: str | None = None) -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="lending-v2.3.1",
        prompt_template_version=prompt_version,
        model_card_version=model_card,
        agent_version="loan-agent@0.4.2",
        end_customer_id="acme-bank",
        subject_id="[REDACTED:SSN]",
        regulatory_regime=[RegulatoryRegime.ECOA, RegulatoryRegime.FCRA],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


def build_loan_denial_trajectory() -> list[Span]:
    """Build the full denial trajectory tree used by both the test and Week 6 example."""
    trajectory_id = "0123456789abcdef" * 2

    root_id = "1111111111111111"
    root = build_span(
        span_id=root_id,
        trajectory_id=trajectory_id,
        parent_span_id=None,
        name="underwrite_application",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(
            decision_rationale="Standard underwriting flow for unsecured personal loan.",
            options_considered=["approve", "approve_with_conditions", "deny", "manual_review"],
            chosen_option="deny",
        ),
        start_time=utc(2026, 5, 17, 12, 0, 0),
        duration_ms=2000,
        inputs={"application_id": "app-9001", "amount_requested": 25000},
        outputs={"decision": "deny"},
        compliance=_make_compliance(),
    )

    retrieval = build_span(
        span_id="2222222222222222",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="fetch_credit_report",
        type=SpanType.RETRIEVAL,
        attributes=RetrievalAttributes(
            source_identifier="experian.consumer-disclosure.v1",
            query="ssn=[REDACTED:SSN]",
            returned_document_ids=["doc-cr-7783"],
            relevance_scores=[1.0],
        ),
        start_time=utc(2026, 5, 17, 12, 0, 0),
        duration_ms=120,
        inputs={"applicant_id": "[REDACTED:SSN]"},
        outputs={"credit_score": 582, "delinquencies_24m": 3},
        compliance=_make_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    policy = build_span(
        span_id="3333333333333333",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="ecoa_protected_class_check",
        type=SpanType.POLICY_CHECK,
        attributes=PolicyCheckAttributes(
            policy_name="ecoa.protected_class.no_use",
            policy_version="v2.3.1",
            result="pass",
            rule_details={"protected_attributes_used_in_decision": []},
        ),
        start_time=utc(2026, 5, 17, 12, 0, 1),
        duration_ms=5,
        compliance=_make_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    model_call = build_span(
        span_id="4444444444444444",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="score_application",
        type=SpanType.MODEL_CALL,
        attributes=ModelCallAttributes(
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            provider="anthropic",
            prompt_template_id="loan_scoring.adverse_action_aware",
            prompt_template_version="v17",
            temperature=0.0,
            max_tokens=1024,
            input_tokens=842,
            output_tokens=183,
            total_tokens=1025,
        ),
        start_time=utc(2026, 5, 17, 12, 0, 2),
        duration_ms=1200,
        inputs={"credit_score": 582, "income": 48000, "dti": 0.46},
        outputs={"recommendation": "deny", "primary_reason": "high_dti", "secondary_reasons": ["delinquencies"]},
        compliance=_make_compliance(
            prompt_version="v17",
            model_card="claude-opus-4-7.lending.v3",
        ),
        parent_content_hash=root.provenance.content_hash,
    )

    planner_decision = build_span(
        span_id="5555555555555555",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="decision",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(
            decision_rationale="Model recommended deny; DTI > policy threshold of 0.43.",
            options_considered=["approve", "deny", "manual_review"],
            chosen_option="deny",
        ),
        start_time=utc(2026, 5, 17, 12, 0, 3),
        duration_ms=2,
        compliance=_make_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    human = build_span(
        span_id="6666666666666666",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="underwriter_review",
        type=SpanType.HUMAN_APPROVAL,
        attributes=HumanApprovalAttributes(
            approver_identity="user:alice@acme-bank.example",
            approver_role="senior_underwriter",
            decision="approved",
            decision_timestamp="2026-05-17T12:00:08Z",
            artifact_reviewed="sha256:" + "f" * 64,
            signature="sig:detached-cose:placeholder",
        ),
        start_time=utc(2026, 5, 17, 12, 0, 5),
        duration_ms=3000,
        compliance=_make_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    side_effect = build_span(
        span_id="7777777777777777",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="send_adverse_action_letter",
        type=SpanType.SIDE_EFFECT,
        attributes=SideEffectAttributes(
            action_type="document.mail",
            target_system="acme-bank.documents-api",
            payload_summary="Adverse action notice citing high DTI and recent delinquencies.",
            idempotency_key="adverse-action-app-9001",
            success=True,
        ),
        start_time=utc(2026, 5, 17, 12, 0, 9),
        duration_ms=400,
        outputs={"document_id": "doc-aa-44102", "delivery_status": "queued"},
        compliance=_make_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    return [root, retrieval, policy, model_call, planner_decision, human, side_effect]


def test_loan_denial_trajectory_well_formed() -> None:
    trajectory = build_loan_denial_trajectory()
    assert_trajectory_well_formed(trajectory)


def test_every_span_carries_required_reporting_fields() -> None:
    trajectory = build_loan_denial_trajectory()
    for span in trajectory:
        assert_reporting_fields_populated(span)


def test_trajectory_contains_each_critical_span_type() -> None:
    trajectory = build_loan_denial_trajectory()
    present = {span.type for span in trajectory}
    # An adverse-action report needs all of these to defend the decision:
    required = {
        SpanType.PLANNER_STEP,
        SpanType.RETRIEVAL,
        SpanType.POLICY_CHECK,
        SpanType.MODEL_CALL,
        SpanType.HUMAN_APPROVAL,
        SpanType.SIDE_EFFECT,
    }
    missing = required - present
    assert not missing, f"adverse-action reporting needs these span types but they are missing: {missing}"


def test_model_call_attributes_capture_adverse_action_requirements() -> None:
    """ECOA adverse-action notices must cite the principal reasons for denial.

    Reporting needs the exact prompt template version, model card version, and
    token counts to defend the decision under SR 11-7 model risk review.
    """
    trajectory = build_loan_denial_trajectory()
    model_calls = [s for s in trajectory if s.type is SpanType.MODEL_CALL]
    assert model_calls
    mc = model_calls[0]
    attrs = mc.attributes
    assert attrs.kind == "model_call"
    # Coverage: every field the adverse-action generator will read.
    assert attrs.model_name
    assert attrs.model_version
    assert attrs.provider
    assert attrs.prompt_template_id
    assert attrs.prompt_template_version
    assert attrs.temperature is not None
    assert attrs.max_tokens is not None
    assert attrs.input_tokens
    assert attrs.output_tokens
    assert attrs.total_tokens
    assert mc.compliance.model_card_version is not None
    assert mc.compliance.prompt_template_version is not None


def test_side_effect_is_distinguishable_from_tool_call() -> None:
    """A regulator asking 'did the agent send the letter?' must find a side_effect span."""
    trajectory = build_loan_denial_trajectory()
    side_effects = [s for s in trajectory if s.type is SpanType.SIDE_EFFECT]
    assert len(side_effects) == 1
    se = side_effects[0]
    assert se.attributes.kind == "side_effect"
    assert se.attributes.target_system
    assert se.attributes.idempotency_key
    assert se.attributes.success is True
