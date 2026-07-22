"""KYC (Know Your Customer) onboarding scenario.

A KYC flow exercises tool_call (sanctions list lookup), sub_agent_invocation
(handing off to a specialized identity-verification sub-agent), and the
critical compliance metadata for BSA/AML.

Flow:
    root planner_step "onboard_customer"
    ├── tool_call "ofac_sanctions_lookup"
    ├── tool_call "verify_government_id"
    ├── sub_agent_invocation "identity_verification_agent"
    │   └── retrieval "fetch_address_history"     [nested under sub-agent]
    │   └── model_call "compare_id_photo"          [nested under sub-agent]
    ├── policy_check "bsa_aml_risk_score"
    └── planner_step "decision"
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
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SubAgentInvocationAttributes,
    ToolCallAttributes,
)

from ._helpers import (
    assert_reporting_fields_populated,
    assert_trajectory_well_formed,
    build_span,
    utc,
)


def _kyc_compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="kyc-v4.1",
        agent_version="kyc-agent@1.2.0",
        end_customer_id="acme-bank",
        subject_id="[REDACTED:passport_number]",
        regulatory_regime=[RegulatoryRegime.BSA_AML, RegulatoryRegime.GLBA],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


def build_kyc_trajectory() -> list[Span]:
    trajectory_id = "fedcba9876543210" * 2
    root_id = "a0a0a0a0a0a0a0a0"
    root = build_span(
        span_id=root_id,
        trajectory_id=trajectory_id,
        parent_span_id=None,
        name="onboard_customer",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(
            decision_rationale="Standard new-account onboarding KYC checks.",
            chosen_option="proceed",
        ),
        start_time=utc(2026, 5, 17, 14, 0, 0),
        duration_ms=4500,
        compliance=_kyc_compliance(),
    )

    ofac = build_span(
        span_id="b1b1b1b1b1b1b1b1",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="ofac_sanctions_lookup",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(
            tool_name="treasury.ofac.lookup",
            tool_schema_version="v2",
            arguments={"name": "[REDACTED:name]", "dob": "[REDACTED:dob]"},
            return_value={"matches": [], "checked_lists": ["SDN", "Consolidated"]},
        ),
        start_time=utc(2026, 5, 17, 14, 0, 0),
        duration_ms=80,
        compliance=ComplianceMetadata(**{**_kyc_compliance().model_dump(), "tool_schema_version": "v2"}),
        parent_content_hash=root.provenance.content_hash,
    )

    id_check = build_span(
        span_id="c2c2c2c2c2c2c2c2",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="verify_government_id",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(
            tool_name="acme.idv.verify",
            tool_schema_version="v3",
            arguments={"document_type": "passport", "document_hash": "sha256:" + "a" * 64},
            return_value={"verified": True, "confidence": 0.97},
        ),
        start_time=utc(2026, 5, 17, 14, 0, 1),
        duration_ms=300,
        compliance=ComplianceMetadata(**{**_kyc_compliance().model_dump(), "tool_schema_version": "v3"}),
        parent_content_hash=root.provenance.content_hash,
    )

    sub_agent = build_span(
        span_id="d3d3d3d3d3d3d3d3",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="identity_verification_agent",
        type=SpanType.SUB_AGENT_INVOCATION,
        attributes=SubAgentInvocationAttributes(
            sub_agent_identity="idv-agent",
            sub_agent_version="0.9.0",
        ),
        start_time=utc(2026, 5, 17, 14, 0, 2),
        duration_ms=1500,
        compliance=_kyc_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    sub_retrieval = build_span(
        span_id="e4e4e4e4e4e4e4e4",
        trajectory_id=trajectory_id,
        parent_span_id=sub_agent.span_id,
        name="fetch_address_history",
        type=SpanType.RETRIEVAL,
        attributes=RetrievalAttributes(
            source_identifier="lexisnexis.address-history.v1",
            query="subject=[REDACTED:passport_number]",
            returned_document_ids=["addr-1", "addr-2", "addr-3"],
            relevance_scores=[1.0, 0.95, 0.91],
        ),
        start_time=utc(2026, 5, 17, 14, 0, 2),
        duration_ms=400,
        compliance=_kyc_compliance(),
        parent_content_hash=sub_agent.provenance.content_hash,
    )

    sub_model = build_span(
        span_id="f5f5f5f5f5f5f5f5",
        trajectory_id=trajectory_id,
        parent_span_id=sub_agent.span_id,
        name="compare_id_photo",
        type=SpanType.MODEL_CALL,
        attributes=ModelCallAttributes(
            model_name="claude-sonnet-4-6",
            model_version="2026-02-15",
            provider="anthropic",
            prompt_template_id="idv.photo_match",
            prompt_template_version="v3",
            temperature=0.0,
            input_tokens=512,
            output_tokens=64,
            total_tokens=576,
        ),
        start_time=utc(2026, 5, 17, 14, 0, 3),
        duration_ms=900,
        compliance=ComplianceMetadata(
            **{
                **_kyc_compliance().model_dump(),
                "prompt_template_version": "v3",
                "model_card_version": "claude-sonnet-4-6.idv.v2",
            }
        ),
        parent_content_hash=sub_agent.provenance.content_hash,
    )

    policy = build_span(
        span_id="0606060606060606",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="bsa_aml_risk_score",
        type=SpanType.POLICY_CHECK,
        attributes=PolicyCheckAttributes(
            policy_name="bsa.aml.customer_risk_score",
            policy_version="v4.1",
            result="pass",
            rule_details={"risk_score": 0.12, "risk_band": "low"},
        ),
        start_time=utc(2026, 5, 17, 14, 0, 4),
        duration_ms=20,
        compliance=_kyc_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    decision = build_span(
        span_id="0707070707070707",
        trajectory_id=trajectory_id,
        parent_span_id=root_id,
        name="decision",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(
            chosen_option="approve",
            decision_rationale="All KYC checks passed; AML risk band low.",
        ),
        start_time=utc(2026, 5, 17, 14, 0, 4),
        duration_ms=3,
        compliance=_kyc_compliance(),
        parent_content_hash=root.provenance.content_hash,
    )

    return [root, ofac, id_check, sub_agent, sub_retrieval, sub_model, policy, decision]


def test_kyc_trajectory_well_formed() -> None:
    trajectory = build_kyc_trajectory()
    assert_trajectory_well_formed(trajectory)


def test_every_span_carries_required_reporting_fields() -> None:
    for span in build_kyc_trajectory():
        assert_reporting_fields_populated(span)


def test_sub_agent_spans_nest_under_sub_agent_invocation() -> None:
    """The sub-agent's work must hang off the SubAgentInvocation, not the root."""
    trajectory = build_kyc_trajectory()
    sub = next(s for s in trajectory if s.type is SpanType.SUB_AGENT_INVOCATION)
    children = [s for s in trajectory if s.parent_span_id == sub.span_id]
    assert len(children) == 2, "expected the sub-agent to have its own retrieval + model_call children"
    assert {c.type for c in children} == {SpanType.RETRIEVAL, SpanType.MODEL_CALL}


def test_tool_schema_versions_recorded_on_tool_calls() -> None:
    """Reporting needs to know which tool schema version the agent used at decision time."""
    trajectory = build_kyc_trajectory()
    tool_calls = [s for s in trajectory if s.type is SpanType.TOOL_CALL]
    assert tool_calls
    for tc in tool_calls:
        assert tc.attributes.tool_schema_version is not None
        # The compliance metadata duplicates this so reporting can filter without joining.
        assert tc.compliance.tool_schema_version == tc.attributes.tool_schema_version
