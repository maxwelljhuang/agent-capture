#!/usr/bin/env python3
r"""The canonical loan-denial agent — Week 6 exit-criterion artifact.

This single example exercises every layer of the SDK end-to-end and
produces a trajectory that satisfies every assertion in
``docs/reporting-fields.md``:

- All eight span types in :class:`agent_capture.schema.SpanType`
  (model_call, tool_call, retrieval, planner_step, sub_agent_invocation,
  human_approval, side_effect, policy_check)
- The Anthropic SDK wrapper (`wrap(client)`) emitting a ``model_call``
- Customer-owned YAML redaction policy (full + HMAC strategies)
- BoundedQueueExporter wrapping a FileExporter — agent's hot path
  never blocks on disk I/O
- Compliance metadata (ECOA + FCRA + BSA/AML regulatory regimes) so the
  downstream adverse-action and BSA/AML report generators have everything
  they need

Flow::

    underwrite_application (planner_step) — ROOT
    ├── fetch_credit_report (retrieval)               [ECOA / FCRA evidence]
    ├── verify_identity (sub_agent_invocation)        [delegates to KYC sub-agent]
    │   ├── ofac_sanctions_lookup (tool_call)
    │   └── verify_government_id (tool_call)
    ├── ecoa_protected_class_check (policy_check)     [protected-class evidence]
    ├── score_application (model_call)                [wrapped Anthropic client]
    ├── decide (planner_step)                         [inner decision]
    ├── underwriter_review (human_approval)           [critical span]
    └── send_adverse_action_letter (side_effect)      [critical span]

Run::

    AGENT_CAPTURE_HMAC_KEY=demo-key \\
        PYTHONPATH=packages/python/src \\
        python packages/python/examples/loan_denial/run.py

After it runs, verify the trajectory satisfies the reporting contract::

    PYTHONPATH=packages/python/src python scripts/verify_trajectory.py trajectory.jsonl
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from agent_capture import configure, traced
from agent_capture.context.propagation import suppress_model_call_capture
from agent_capture.exporter import BoundedQueueExporter, FileExporter
from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap as wrap_anthropic
from agent_capture.redaction import RedactionFilter, load_policy
from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.compliance import (
    DataClassification,
    RegulatoryRegime,
    RetentionClass,
)
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
    SubAgentInvocationAttributes,
    ToolCallAttributes,
)

# ---- Compliance metadata --------------------------------------------------


def lending_compliance(policy_version: str) -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active=policy_version,
        agent_version="loan-agent@1.0.0",
        end_customer_id="acme-bank",
        # subject_id is what the regulator references; in production it is
        # set by the agent at trajectory open and is itself an HMAC of the
        # applicant's primary key so it can be re-fingerprinted later.
        subject_id="customer-77013",
        regulatory_regime=[
            RegulatoryRegime.ECOA,
            RegulatoryRegime.FCRA,
            RegulatoryRegime.BSA_AML,
            RegulatoryRegime.SR_11_7,
        ],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


# ---- Stand-in Anthropic client (no anthropic SDK required for the demo) ----


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, recommendation: str) -> None:
        self.id = "msg_demo_1"
        self.model = "claude-opus-4-7"
        self.content = [{"type": "text", "text": recommendation}]
        self.usage = _Usage(input_tokens=842, output_tokens=183)
        self.stop_reason = "end_turn"


class _Messages:
    def create(self, **kwargs: Any) -> _Response:
        return _Response("deny: high DTI and recent delinquencies")


class _AnthropicShape:
    def __init__(self) -> None:
        self.messages = _Messages()


# ---- Steps ----------------------------------------------------------------


@traced(
    type=SpanType.RETRIEVAL,
    name="fetch_credit_report",
    attributes=RetrievalAttributes(
        source_identifier="experian.consumer-disclosure.v1",
        returned_document_ids=["doc-cr-7783"],
        relevance_scores=[1.0],
    ),
)
def fetch_credit_report(applicant: dict[str, Any]) -> dict[str, Any]:
    return {
        "credit_score": 582,
        "delinquencies_24m": 3,
        # Free text the pattern recognizers will catch:
        "narrative": (
            f"Applicant SSN {applicant['ssn']} has 3 delinquencies. Routing 011000015 used for direct deposit."
        ),
    }


@traced(
    type=SpanType.TOOL_CALL,
    name="ofac_sanctions_lookup",
    attributes=ToolCallAttributes(
        tool_name="treasury.ofac.lookup",
        tool_schema_version="v2",
    ),
)
def ofac_lookup(name: str, dob: str) -> dict[str, Any]:
    return {"matches": [], "checked_lists": ["SDN", "Consolidated"]}


@traced(
    type=SpanType.TOOL_CALL,
    name="verify_government_id",
    attributes=ToolCallAttributes(
        tool_name="acme.idv.verify",
        tool_schema_version="v3",
    ),
)
def verify_id(document_type: str) -> dict[str, Any]:
    return {"verified": True, "confidence": 0.97}


@traced(
    type=SpanType.SUB_AGENT_INVOCATION,
    name="verify_identity",
    attributes=SubAgentInvocationAttributes(
        sub_agent_identity="kyc-agent",
        sub_agent_version="0.9.0",
    ),
)
def verify_identity(applicant: dict[str, Any]) -> dict[str, Any]:
    # The sub-agent's work nests under this span automatically via the
    # @traced decorator + ContextVar-backed parent pointer.
    ofac = ofac_lookup(applicant["name"], applicant["dob"])
    idv = verify_id("passport")
    return {"ofac_clean": not ofac["matches"], "id_verified": idv["verified"]}


@traced(
    type=SpanType.POLICY_CHECK,
    name="ecoa_protected_class_check",
    attributes=PolicyCheckAttributes(
        policy_name="ecoa.protected_class.no_use",
        policy_version="v2.3.1",
        result="pass",
        rule_details={"protected_attributes_used_in_decision": []},
    ),
)
def ecoa_check() -> dict[str, Any]:
    return {"compliant": True}


@traced(
    type=SpanType.POLICY_CHECK,
    name="bsa_aml_risk_score",
    attributes=PolicyCheckAttributes(
        policy_name="bsa.aml.customer_risk_score",
        policy_version="v4.1",
        result="pass",
        rule_details={"risk_score": 0.12, "risk_band": "low"},
    ),
)
def bsa_aml_check() -> dict[str, Any]:
    return {"risk_band": "low"}


def model_score(client: Any, prompt: str) -> dict[str, Any]:
    """Wrap the model call in @traced so we can attach full SR 11-7 attributes.

    The wrapped Anthropic client would normally emit a model_call span on
    its own. We suppress it here so the explicit traced() owns the span
    with the richer attributes set (prompt_template_version, model_card_
    version, etc. that the wrapper cannot know from the request kwargs).
    This is the same dedup contract the LangGraph adapter uses.
    """
    scope = traced(
        type=SpanType.MODEL_CALL,
        name="score_application",
        attributes=ModelCallAttributes(
            model_name="claude-opus-4-7",
            model_version="2026-03-01",
            provider="anthropic",
            prompt_template_id="loan_scoring.adverse_action_aware",
            prompt_template_version="v17",
            temperature=0.0,
            max_tokens=1024,
        ),
        compliance=lending_compliance("lending-v2.3.1").model_copy(
            update={
                "prompt_template_version": "v17",
                "model_card_version": "claude-opus-4-7.lending.v3",
            }
        ),
        inputs={"prompt": prompt},
    )
    with scope as open_span:
        with suppress_model_call_capture():
            response = client.messages.create(
                model="claude-opus-4-7",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                temperature=0.0,
            )
        # Patch token counts into the span attributes before close.
        if open_span is not None:
            open_span.attributes = open_span.attributes.model_copy(
                update={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                }
            )
        outputs = {
            "text": response.content[0]["text"],
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        scope.set_outputs(outputs)
    return outputs


@traced(
    type=SpanType.HUMAN_APPROVAL,
    name="underwriter_review",
    attributes=HumanApprovalAttributes(
        approver_identity="user:alice@acme-bank.example",
        approver_role="senior_underwriter",
        decision="approved",
        decision_timestamp="2026-05-18T12:00:08Z",
        artifact_reviewed="sha256:" + "f" * 64,
        signature="sig:detached-cose:placeholder",
    ),
)
def underwriter_review() -> dict[str, Any]:
    return {"approved": True, "rationale": "Confirms model recommendation."}


@traced(
    type=SpanType.SIDE_EFFECT,
    name="send_adverse_action_letter",
    attributes=SideEffectAttributes(
        action_type="document.mail",
        target_system="acme-bank.documents-api",
        payload_summary="Adverse action notice citing high DTI and recent delinquencies.",
        idempotency_key="adverse-action-app-9001",
        success=True,
    ),
)
def send_adverse_action_letter(account_number: str) -> dict[str, Any]:
    return {"document_id": "doc-aa-44102", "delivery_status": "queued"}


# ---- Orchestration -------------------------------------------------------


def underwrite(client: Any, applicant: dict[str, Any]) -> str:
    """Top-level planner step. Emits the root span via context manager."""
    with traced(type=SpanType.PLANNER_STEP, name="underwrite_application"):
        credit = fetch_credit_report(applicant)
        verify_identity(applicant)
        ecoa_check()
        bsa_aml_check()

        prompt = (
            f"Underwrite application. credit_score={credit['credit_score']} "
            f"dti=0.46. Output: recommendation + primary_reason + secondary_reasons."
        )
        model_result = model_score(client, prompt)

        with traced(
            type=SpanType.PLANNER_STEP,
            name="decide",
            attributes=PlannerStepAttributes(
                decision_rationale=(
                    f"Model recommended deny. Credit score {credit['credit_score']} below threshold; "
                    f"DTI 0.46 above policy 0.43."
                ),
                options_considered=["approve", "deny", "manual_review"],
                chosen_option="deny",
            ),
        ):
            pass

        underwriter_review()
        send_adverse_action_letter(account_number=applicant["account_number"])

        return f"decision=deny; model_said={model_result['text']!r}"


# ---- Entry point ---------------------------------------------------------


def main() -> int:
    os.environ.setdefault("AGENT_CAPTURE_HMAC_KEY", "demo-key")
    here = Path(__file__).parent
    out_path = Path("trajectory.jsonl").resolve()
    out_path.unlink(missing_ok=True)

    policy = load_policy(here / "policy.yaml")
    redaction = RedactionFilter(policy=policy)

    pipeline = BoundedQueueExporter(FileExporter(out_path), max_size=1_000)
    configure(
        exporter=pipeline,
        default_compliance=lending_compliance(policy.version),
        redaction_filter=redaction,
    )

    # The agent's model SDK is wrapped exactly once at construction time.
    client = wrap_anthropic(_AnthropicShape())

    applicant = {
        "name": "Jane Doe",
        "dob": "DOB: 04/12/1989",
        "ssn": "123-45-6789",
        "account_number": "12345678",
    }
    decision = underwrite(client, applicant)
    pipeline.shutdown(timeout=5.0)

    print(f"Wrote trajectory to {out_path}")
    print(decision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
