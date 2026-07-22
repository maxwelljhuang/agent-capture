"""Fraud-flag scenario — exercises error paths and a cancelled span.

Flow:
    root planner_step "evaluate_transaction"
    ├── retrieval "fetch_transaction_history"  [errors out]
    ├── retrieval "fetch_transaction_history_fallback"
    ├── model_call "fraud_score"  [cancelled — fallback retrieval timed out]
    ├── tool_call "rules_engine_score" [used as fallback path]
    ├── policy_check "velocity_check"
    └── side_effect "freeze_account"  [critical]
"""

from __future__ import annotations

from agent_capture.schema import (
    ComplianceMetadata,
    ErrorInfo,
    RegulatoryRegime,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
    ToolCallAttributes,
)

from ._helpers import (
    assert_reporting_fields_populated,
    assert_trajectory_well_formed,
    build_span,
    utc,
)


def _fraud_compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="fraud-v1.7",
        agent_version="fraud-agent@2.0.1",
        end_customer_id="acme-bank",
        subject_id="acct-77013",
        regulatory_regime=[RegulatoryRegime.BSA_AML, RegulatoryRegime.UDAAP],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


def build_fraud_trajectory() -> list[Span]:
    trajectory_id = "1357913579135791" * 2
    root_id = "abcdabcdabcdabcd"
    root = build_span(
        span_id=root_id,
        trajectory_id=trajectory_id,
        parent_span_id=None,
        name="evaluate_transaction",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(
            decision_rationale="High-amount wire transfer flagged for evaluation.",
            options_considered=["allow", "challenge", "block", "freeze"],
            chosen_option="freeze",
        ),
        start_time=utc(2026, 5, 17, 18, 0, 0),
        duration_ms=3000,
        compliance=_fraud_compliance(),
    )

    failed_retrieval = build_span(
        span_id="aaaaaaaaaaaaaaa1",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="fetch_transaction_history",
        type=SpanType.RETRIEVAL,
        attributes=RetrievalAttributes(
            source_identifier="warehouse.transactions.primary",
        ),
        start_time=utc(2026, 5, 17, 18, 0, 0),
        duration_ms=5000,
        status=SpanStatus.ERROR,
        error=ErrorInfo(
            error_type="ConnectionTimeoutError",
            message="warehouse.transactions.primary unreachable after 5s",
        ),
        compliance=_fraud_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    fallback_retrieval = build_span(
        span_id="aaaaaaaaaaaaaaa2",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="fetch_transaction_history_fallback",
        type=SpanType.RETRIEVAL,
        attributes=RetrievalAttributes(
            source_identifier="warehouse.transactions.replica",
            returned_document_ids=["txn-1", "txn-2", "txn-3"],
            relevance_scores=[1.0, 1.0, 1.0],
        ),
        start_time=utc(2026, 5, 17, 18, 0, 5),
        duration_ms=400,
        compliance=_fraud_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    cancelled_model_call = build_span(
        span_id="aaaaaaaaaaaaaaa3",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="fraud_score",
        type=SpanType.MODEL_CALL,
        attributes=ModelCallAttributes(
            model_name="claude-opus-4-7",
            provider="anthropic",
            prompt_template_id="fraud_score.v3",
            prompt_template_version="v3",
        ),
        start_time=utc(2026, 5, 17, 18, 0, 6),
        duration_ms=200,
        status=SpanStatus.CANCELLED,
        compliance=ComplianceMetadata(
            **{
                **_fraud_compliance().model_dump(),
                "prompt_template_version": "v3",
                "model_card_version": "claude-opus-4-7.fraud.v1",
            }
        ),
        parent_content_hash=root.provenance.content_hash,
    )

    rules_call = build_span(
        span_id="aaaaaaaaaaaaaaa4",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="rules_engine_score",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(
            tool_name="acme.fraud.rules-engine",
            tool_schema_version="v8",
            arguments={"transaction_id": "txn-1"},
            return_value={"score": 0.97, "rules_fired": ["velocity_24h", "new_payee", "round_amount"]},
        ),
        start_time=utc(2026, 5, 17, 18, 0, 6),
        duration_ms=30,
        compliance=ComplianceMetadata(**{**_fraud_compliance().model_dump(), "tool_schema_version": "v8"}),
        parent_content_hash=root.provenance.content_hash,
    )

    velocity_check = build_span(
        span_id="aaaaaaaaaaaaaaa5",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="velocity_check",
        type=SpanType.POLICY_CHECK,
        attributes=PolicyCheckAttributes(
            policy_name="fraud.velocity.high_amount_wire",
            policy_version="v1.7",
            result="fail",
            rule_details={"threshold": 10000, "observed": 32500, "window_hours": 24},
        ),
        start_time=utc(2026, 5, 17, 18, 0, 7),
        duration_ms=10,
        compliance=_fraud_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    freeze = build_span(
        span_id="aaaaaaaaaaaaaaa6",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="freeze_account",
        type=SpanType.SIDE_EFFECT,
        attributes=SideEffectAttributes(
            action_type="account.freeze",
            target_system="acme-bank.core-banking",
            payload_summary="Temporary hold pending manual review.",
            idempotency_key="freeze-acct-77013-20260517",
            success=True,
        ),
        start_time=utc(2026, 5, 17, 18, 0, 8),
        duration_ms=120,
        outputs={"hold_id": "hold-9921", "review_queue": "fraud-tier-2"},
        compliance=_fraud_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    return [
        root,
        failed_retrieval,
        fallback_retrieval,
        cancelled_model_call,
        rules_call,
        velocity_check,
        freeze,
    ]


def test_fraud_trajectory_well_formed() -> None:
    trajectory = build_fraud_trajectory()
    assert_trajectory_well_formed(trajectory)


def test_every_span_carries_required_reporting_fields() -> None:
    for span in build_fraud_trajectory():
        assert_reporting_fields_populated(span)


def test_error_status_preserves_structured_error_info() -> None:
    trajectory = build_fraud_trajectory()
    errored = [s for s in trajectory if s.status is SpanStatus.ERROR]
    assert errored, "fraud scenario should include an errored span"
    for s in errored:
        assert s.error is not None
        assert s.error.error_type
        assert s.error.message


def test_cancelled_status_is_first_class() -> None:
    """Cancelled is distinct from error — reporting cares about the difference."""
    trajectory = build_fraud_trajectory()
    cancelled = [s for s in trajectory if s.status is SpanStatus.CANCELLED]
    assert len(cancelled) == 1
    assert cancelled[0].error is None  # cancelled spans should not carry error info


def test_critical_side_effect_present() -> None:
    """A fraud freeze is a critical side_effect — the exporter must never drop these."""
    trajectory = build_fraud_trajectory()
    side_effects = [s for s in trajectory if s.type is SpanType.SIDE_EFFECT]
    assert len(side_effects) == 1
    assert side_effects[0].attributes.action_type == "account.freeze"
    assert side_effects[0].attributes.success is True
