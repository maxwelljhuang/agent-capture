"""End-to-end: recorder + enforcement gate + ledger.

A fail-closed rule blocks a real ``@traced`` side_effect through the inline
verdict service; the blocked (status=error, success=False) span is then shipped
to the ledger and read back. Proves the L1→L5→L2 wiring end to end. Skipped in
the ledger-only venv (no enforcement/reporter packages).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio

pytest.importorskip("agent_capture_enforcement")

from agent_capture import traced
from agent_capture.enforcement import EnforcementBlocked, set_gate
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span, SpanStatus, SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import PlannerStepAttributes, SideEffectAttributes
from agent_capture.span.builder import SpanBuilder
from agent_capture_enforcement.client import EnforcementClient
from agent_capture_enforcement.config import Settings as EnfSettings
from agent_capture_enforcement.config import set_settings as set_enf_settings
from agent_capture_enforcement.service.app import create_app as create_enforcement_app
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app as create_ledger_app
from agent_capture_ledger.storage.engine import get_session_factory, session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import generate_secret, generate_token_id, hash_secret

pytestmark = pytest.mark.e2e

CUSTOMER = "demo-co"

_FAIL_CLOSED_RULE = (
    "version: enf-block-v1\nrules:\n"
    "  - id: deny_wire\n    span_type: side_effect\n"
    "    evaluator: action_type_allowed\n    params: { deny: [payment.wire] }\n"
    "    mode: blocking\n    failure_mode: fail_closed\n"
)


class _Capture(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_enforcement():  # type: ignore[no-untyped-def]
    yield
    set_gate(None)
    set_enf_settings(None)


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="lending-v1",
        agent_version="loan-agent@0.1.0",
        end_customer_id=CUSTOMER,
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )


@pytest_asyncio.fixture
async def ingest_token(session):  # type: ignore[no-untyped-def]
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
async def reader_token(session):  # type: ignore[no-untyped-def]
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
def ledger_client(session):  # type: ignore[no-untyped-def]
    app = create_ledger_app()

    async def _override():  # type: ignore[no-untyped-def]
        async with get_session_factory()() as s:
            yield s

    app.dependency_overrides[session_dependency] = _override
    return TestClient(app)


def test_blocked_side_effect_flows_to_ledger(ledger_client, ingest_token, reader_token, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # 1) Stand up the verdict service with a fail-closed rule, register the gate.
    rules = tmp_path / "rules.yaml"
    rules.write_text(_FAIL_CLOSED_RULE, encoding="utf-8")
    set_enf_settings(EnfSettings(rules_path=rules))
    set_gate(EnforcementClient(client=TestClient(create_enforcement_app())))

    # 2) Run a real recorder trajectory; the payment.wire side_effect is blocked.
    cap = _Capture()
    builder = SpanBuilder(cap, default_compliance=_compliance())
    blocked = False
    with traced(type=SpanType.PLANNER_STEP, name="underwrite", attributes=PlannerStepAttributes(), builder=builder):
        try:

            @traced(
                type=SpanType.SIDE_EFFECT,
                name="wire_funds",
                attributes=SideEffectAttributes(action_type="payment.wire", target_system="bank.api", success=True),
                builder=builder,
            )
            def wire_funds() -> str:
                return "sent"

            wire_funds()
        except EnforcementBlocked:
            blocked = True

    assert blocked is True
    se = next(s for s in cap.spans if s.type is SpanType.SIDE_EFFECT)
    assert se.status is SpanStatus.ERROR
    assert se.attributes.success is False  # type: ignore[union-attr]

    # 3) Ship the whole trajectory to the ledger (HTTPExporter wire shape).
    body = {"spans": [json.loads(s.model_dump_json(exclude_none=False)) for s in cap.spans]}
    r = ledger_client.post("/spans", json=body, headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == len(cap.spans)

    # 4) Read it back: the blocked side_effect is stored with status=error.
    tid = se.trajectory_id
    read = ledger_client.get(f"/trajectories/{tid}/spans", headers={"Authorization": f"Bearer {reader_token}"})
    assert read.status_code == 200, read.text
    spans = read.json()["spans"]
    stored = next(s for s in spans if s["type"] == "side_effect")
    assert stored["status"] == "error"
    assert stored["attributes"]["success"] is False
    assert datetime.fromisoformat(stored["start_time"]).tzinfo is not None  # sanity: tz-aware
    _ = datetime.now(UTC)  # marker that the trajectory used near-now timestamps
