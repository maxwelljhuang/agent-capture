"""Shared fixtures for reporter tests.

The canonical input is the loan-denial trajectory — the same shape as
``packages/python/tests/scenarios/test_loan_approval.py``. We rebuild it here
(rather than import across packages) so the reporter's test suite is
self-contained and depends only on the public ``agent_capture.schema`` surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    RegulatoryRegime,
    Span,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
    TypedAttributes,
)

TRAJECTORY_ID = "0123456789abcdef" * 2


def utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def _compliance(*, prompt_version: str | None = None, model_card: str | None = None) -> ComplianceMetadata:
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


def build_span(
    *,
    span_id: str,
    parent_span_id: str | None,
    name: str,
    type: SpanType,
    attributes: TypedAttributes,
    start_time: datetime,
    duration_ms: int = 10,
    inputs: Any | None = None,
    outputs: Any | None = None,
    compliance: ComplianceMetadata | None = None,
    parent_content_hash: str | None = None,
    trajectory_id: str = TRAJECTORY_ID,
) -> Span:
    """Build a fully-populated Span with a computed content hash."""
    end_time = start_time + timedelta(milliseconds=duration_ms)
    placeholder = Span(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trajectory_id=trajectory_id,
        name=name,
        type=type,
        start_time=start_time,
        end_time=end_time,
        attributes=attributes,
        inputs=inputs,
        outputs=outputs,
        compliance=compliance or _compliance(),
        provenance=ProvenanceFields(content_hash="0" * 64, parent_content_hash=parent_content_hash),
    )
    real_hash = content_hash(placeholder)
    return placeholder.model_copy(
        update={"provenance": ProvenanceFields(content_hash=real_hash, parent_content_hash=parent_content_hash)}
    )


def build_loan_denial_trajectory() -> list[Span]:
    """The full denial trajectory: planner root + retrieval, policy, model, human, side_effect."""
    root_id = "1111111111111111"
    root = build_span(
        span_id=root_id,
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
    )
    retrieval = build_span(
        span_id="2222222222222222",
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
        parent_content_hash=root.provenance.content_hash,
    )
    policy = build_span(
        span_id="3333333333333333",
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
        parent_content_hash=root.provenance.content_hash,
    )
    model_call = build_span(
        span_id="4444444444444444",
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
        compliance=_compliance(prompt_version="v17", model_card="claude-opus-4-7.lending.v3"),
        parent_content_hash=root.provenance.content_hash,
    )
    planner_decision = build_span(
        span_id="5555555555555555",
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
        parent_content_hash=root.provenance.content_hash,
    )
    human = build_span(
        span_id="6666666666666666",
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
        parent_content_hash=root.provenance.content_hash,
    )
    side_effect = build_span(
        span_id="7777777777777777",
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
        parent_content_hash=root.provenance.content_hash,
    )
    return [root, retrieval, policy, model_call, planner_decision, human, side_effect]


@pytest.fixture
def loan_denial_spans() -> list[Span]:
    return build_loan_denial_trajectory()


@pytest.fixture
def generated_at() -> datetime:
    """A fixed timestamp so manifests are byte-reproducible in tests."""
    return datetime(2026, 5, 18, 9, 30, 0, tzinfo=UTC)


# --- SR 11-7 corpus fixtures ------------------------------------------------


def build_model_call_trajectory(
    *,
    trajectory_id: str,
    model_name: str,
    model_version: str | None,
    provider: str = "anthropic",
    model_card_version: str | None = None,
    start_time: datetime,
    total_tokens: int | None = 1000,
    prompt_version: str = "v17",
    end_customer_id: str = "acme-bank",
) -> list[Span]:
    """A minimal trajectory: a planner root with one model_call child.

    Enough to exercise the SR 11-7 inventory (which only reads model_call spans).
    """
    root_id = trajectory_id[:16]
    mc_id = trajectory_id[:14] + "ac"  # distinct from root_id (our test ids repeat a 2-char block)
    compliance = ComplianceMetadata(
        policy_version_active="lending-v2.3.1",
        prompt_template_version=prompt_version,
        model_card_version=model_card_version,
        agent_version="loan-agent@0.4.2",
        end_customer_id=end_customer_id,
        regulatory_regime=[RegulatoryRegime.SR_11_7],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )
    root = build_span(
        span_id=root_id,
        parent_span_id=None,
        name="underwrite_application",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(chosen_option="deny"),
        start_time=start_time,
        trajectory_id=trajectory_id,
        compliance=compliance,
    )
    model_call = build_span(
        span_id=mc_id,
        parent_span_id=root_id,
        name="score_application",
        type=SpanType.MODEL_CALL,
        attributes=ModelCallAttributes(
            model_name=model_name,
            model_version=model_version,
            provider=provider,
            prompt_template_id="loan_scoring.adverse_action_aware",
            prompt_template_version=prompt_version,
            temperature=0.0,
            max_tokens=1024,
            input_tokens=(total_tokens - 100) if total_tokens is not None else None,
            output_tokens=100 if total_tokens is not None else None,
            total_tokens=total_tokens,
        ),
        start_time=start_time,
        trajectory_id=trajectory_id,
        compliance=compliance,
        parent_content_hash=root.provenance.content_hash,
    )
    return [root, model_call]


def build_inventory_corpus() -> list[list[Span]]:
    """A 4-trajectory corpus covering 3 distinct models with varied governance.

    - Model A (claude-opus-4-7 @ 2026-03-01, card v3): governed + validated, 2 uses.
    - Model B (claude-opus-4-7 @ 2026-02-01, NO card): ungoverned.
    - Model C (claude-sonnet-4-6 @ 2026-01-10, card v2): card not in registry.
    """
    return [
        build_model_call_trajectory(
            trajectory_id="a1" * 16,
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            model_card_version="claude-opus-4-7.lending.v3",
            start_time=utc(2026, 3, 1, 10, 0, 0),
            total_tokens=1000,
        ),
        build_model_call_trajectory(
            trajectory_id="a2" * 16,
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            model_card_version="claude-opus-4-7.lending.v3",
            start_time=utc(2026, 3, 15, 10, 0, 0),
            total_tokens=1200,
        ),
        build_model_call_trajectory(
            trajectory_id="b1" * 16,
            model_name="claude-opus-4-7",
            model_version="2026-02-01",
            model_card_version=None,
            start_time=utc(2026, 2, 1, 10, 0, 0),
            total_tokens=900,
        ),
        build_model_call_trajectory(
            trajectory_id="c1" * 16,
            model_name="claude-sonnet-4-6",
            model_version="2026-01-10",
            model_card_version="claude-sonnet-4-6.lending.v2",
            start_time=utc(2026, 1, 10, 10, 0, 0),
            total_tokens=None,
        ),
    ]


@pytest.fixture
def inventory_corpus() -> list[list[Span]]:
    return build_inventory_corpus()


@pytest.fixture
def multi_tenant_corpus() -> list[list[Span]]:
    """Two trajectories using the same model under two different tenants."""
    return [
        build_model_call_trajectory(
            trajectory_id="d1" * 16,
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            model_card_version="claude-opus-4-7.lending.v3",
            start_time=utc(2026, 3, 1),
            end_customer_id="acme-bank",
        ),
        build_model_call_trajectory(
            trajectory_id="d2" * 16,
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            model_card_version="claude-opus-4-7.lending.v3",
            start_time=utc(2026, 3, 2),
            end_customer_id="beta-credit-union",
        ),
    ]


@pytest.fixture
def governance_registry_dict() -> dict[str, object]:
    """Registry with an entry for model A's card only (v3, validated)."""
    return {
        "models": [
            {
                "model_card_version": "claude-opus-4-7.lending.v3",
                "provider": "anthropic",
                "model_name": "claude-opus-4-7",
                "intended_use": "Unsecured personal loan underwriting risk score.",
                "risk_tier": "tier_1",
                "validation_status": "validated",
                "last_validated": "2026-02-01",
                "valid_until": "2027-02-01",
                "limitations": "Not for commercial lending.",
                "monitoring": "owner=mrm@acme-bank; cadence=quarterly; status=active",
            }
        ]
    }
