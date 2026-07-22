"""End-to-end across all three layers: recorder → ledger → reporter.

Builds a recorder-canonical loan-denial trajectory, ships it to the in-process
ledger (the ingest path re-verifies every content_hash), then reads it back
*through the reporter's ledger source* and renders both regulatory artifacts.
This is the proof that the production pipeline runs end to end without files.

Skipped automatically in the ledger-only CI venv (which has no reporter); runs
in the dedicated ``e2e`` job that installs the whole workspace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

pytest.importorskip("agent_capture_reporter")

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
from agent_capture_reporter.common.corpus import ReportingPeriod
from agent_capture_reporter.common.ledger_source import (
    LedgerClient,
    load_corpus_from_ledger,
    load_trajectory_from_ledger,
)
from agent_capture_reporter.report import render_adverse_action, render_model_inventory
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceEntry, ModelGovernanceRegistry
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.storage.engine import get_session_factory, session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
)

pytestmark = pytest.mark.e2e

CUSTOMER = "demo-co"
TRAJECTORY_ID = "0123456789abcdef" * 2
GENERATED_AT = datetime(2026, 5, 18, 9, 30, 0, tzinfo=UTC)
CARD = "claude-opus-4-7.lending.v3"


def _compliance(*, prompt: str | None = None, card: str | None = None) -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="lending-v2.3.1",
        prompt_template_version=prompt,
        model_card_version=card,
        agent_version="loan-agent@0.4.2",
        end_customer_id=CUSTOMER,
        subject_id="[REDACTED:SSN]",
        regulatory_regime=[RegulatoryRegime.ECOA, RegulatoryRegime.FCRA, RegulatoryRegime.SR_11_7],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


def _span(
    *,
    span_id: str,
    parent: str | None,
    name: str,
    type_: SpanType,
    attributes: TypedAttributes,
    start: datetime,
    compliance: ComplianceMetadata,
    inputs: Any | None = None,
    outputs: Any | None = None,
    parent_hash: str | None = None,
) -> Span:
    placeholder = Span(
        span_id=span_id,
        parent_span_id=parent,
        trajectory_id=TRAJECTORY_ID,
        name=name,
        type=type_,
        start_time=start,
        end_time=start + timedelta(milliseconds=50),
        attributes=attributes,
        inputs=inputs,
        outputs=outputs,
        compliance=compliance,
        provenance=ProvenanceFields(content_hash="0" * 64, parent_content_hash=parent_hash),
    )
    return placeholder.model_copy(
        update={"provenance": ProvenanceFields(content_hash=content_hash(placeholder), parent_content_hash=parent_hash)}
    )


def _loan_denial(t: datetime) -> list[Span]:
    # start_time must fall in an existing monthly partition of the ledger's
    # `spans` table, so the caller passes a near-now timestamp.
    root = _span(
        span_id="1111111111111111",
        parent=None,
        name="underwrite_application",
        type_=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(chosen_option="deny", decision_rationale="DTI over threshold."),
        start=t,
        compliance=_compliance(),
        outputs={"decision": "deny"},
    )
    rh = root.provenance.content_hash
    retrieval = _span(
        span_id="2222222222222222",
        parent=root.span_id,
        name="fetch_credit_report",
        type_=SpanType.RETRIEVAL,
        attributes=RetrievalAttributes(source_identifier="experian.consumer-disclosure.v1"),
        start=t,
        compliance=_compliance(),
        parent_hash=rh,
    )
    policy = _span(
        span_id="3333333333333333",
        parent=root.span_id,
        name="ecoa_protected_class_check",
        type_=SpanType.POLICY_CHECK,
        attributes=PolicyCheckAttributes(
            policy_name="ecoa.protected_class.no_use", policy_version="v2.3.1", result="pass"
        ),
        start=t,
        compliance=_compliance(),
        parent_hash=rh,
    )
    model = _span(
        span_id="4444444444444444",
        parent=root.span_id,
        name="score_application",
        type_=SpanType.MODEL_CALL,
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
        start=t,
        compliance=_compliance(prompt="v17", card=CARD),
        outputs={"recommendation": "deny", "primary_reason": "high_dti", "secondary_reasons": ["delinquencies"]},
        parent_hash=rh,
    )
    human = _span(
        span_id="6666666666666666",
        parent=root.span_id,
        name="underwriter_review",
        type_=SpanType.HUMAN_APPROVAL,
        attributes=HumanApprovalAttributes(
            approver_identity="user:alice@demo-co.example",
            approver_role="senior_underwriter",
            decision="approved",
            decision_timestamp=t.isoformat(),
            artifact_reviewed="sha256:" + "f" * 64,
        ),
        start=t,
        compliance=_compliance(),
        parent_hash=rh,
    )
    side_effect = _span(
        span_id="7777777777777777",
        parent=root.span_id,
        name="send_adverse_action_letter",
        type_=SpanType.SIDE_EFFECT,
        attributes=SideEffectAttributes(
            action_type="document.mail",
            target_system="demo-co.documents-api",
            idempotency_key="adverse-action-app-9001",
            success=True,
        ),
        start=t,
        compliance=_compliance(),
        parent_hash=rh,
    )
    return [root, retrieval, policy, model, human, side_effect]


@pytest_asyncio.fixture
async def ingest_token(session):
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id=CUSTOMER,
        label="e2e",
        created_by="pytest",
    )
    await session.commit()
    return f"{tid}.{sec}"


@pytest_asyncio.fixture
async def reader_token(session):
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="reader",
        end_customer_id=CUSTOMER,
        label="e2e",
        created_by="pytest",
    )
    await session.commit()
    return f"{tid}.{sec}"


@pytest.fixture
def client(session):
    app = create_app()

    async def _override():
        async with get_session_factory()() as s:
            yield s

    app.dependency_overrides[session_dependency] = _override
    return TestClient(app)


def test_recorder_to_ledger_to_reporter(client, ingest_token, reader_token):
    now = datetime.now(UTC)
    spans = _loan_denial(now)
    # Recorder → ledger: ship the trajectory as the HTTPExporter would (one batch).
    body = {"spans": [json.loads(s.model_dump_json(exclude_none=False)) for s in spans]}
    r = client.post("/spans", json=body, headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == len(spans)

    # Ledger → reporter: read back through the reporter's ledger source (TestClient
    # is an httpx.Client subclass, so LedgerClient drives the in-process app).
    ledger = LedgerClient(token=reader_token, client=client)

    # ECOA adverse-action notice.
    trajectory = load_trajectory_from_ledger(ledger, TRAJECTORY_ID)
    notice = render_adverse_action(trajectory, generated_at=GENERATED_AT, with_pdf=False)
    flat = " ".join(notice.html.split())
    assert "Your application for credit was denied." in flat
    assert "experian.consumer-disclosure.v1" in flat
    assert notice.manifest.hash_chain_verified is True
    assert notice.manifest.completeness_score == 1.0
    assert notice.manifest.gaps == []

    # SR 11-7 model inventory over the period containing the decision.
    registry = ModelGovernanceRegistry(
        source="e2e-registry",
        entries=[
            ModelGovernanceEntry(
                model_card_version=CARD,
                provider="anthropic",
                model_name="claude-opus-4-7",
                intended_use="Loan underwriting risk score.",
                risk_tier="tier_1",
                validation_status="validated",
            )
        ],
    )
    period = ReportingPeriod(start=now - timedelta(days=1), end=now + timedelta(days=1))
    corpus = load_corpus_from_ledger(ledger, period)
    inventory = render_model_inventory(corpus, registry, period, generated_at=GENERATED_AT, with_pdf=False)
    assert inventory.manifest.total_models == 1
    assert inventory.manifest.governed_models == 1
    assert inventory.manifest.hash_chain_verified is True
