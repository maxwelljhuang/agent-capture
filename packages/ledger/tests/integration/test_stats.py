"""GET /stats — tenant-scoped aggregate counts over the query API."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from agent_capture.schema import (
    ComplianceMetadata,
    ErrorInfo,
    ProvenanceFields,
    RegulatoryRegime,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    SideEffectAttributes,
    TypedAttributes,
)
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.storage.engine import get_session_factory, session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import generate_secret, generate_token_id, hash_secret
from tests._helpers import envelope, make_span

pytestmark = pytest.mark.integration

CUSTOMER = "demo-co"


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="agent@0.1.0",
        end_customer_id=CUSTOMER,
        regulatory_regime=[RegulatoryRegime.ECOA],
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _span(
    *,
    span_id: str,
    parent: str | None,
    type_: SpanType,
    attrs: TypedAttributes,
    start: datetime,
    status: SpanStatus = SpanStatus.OK,
    error: ErrorInfo | None = None,
    parent_hash: str | None = None,
) -> Span:
    placeholder = Span(
        span_id=span_id,
        parent_span_id=parent,
        trajectory_id="c" * 32,
        name=type_.value,
        type=type_,
        start_time=start,
        end_time=start + timedelta(milliseconds=5),
        status=status,
        error=error,
        attributes=attrs,
        compliance=_compliance(),
        provenance=ProvenanceFields(content_hash="0" * 64, parent_content_hash=parent_hash),
    )
    return placeholder.model_copy(
        update={"provenance": ProvenanceFields(content_hash=content_hash(placeholder), parent_content_hash=parent_hash)}
    )


def _trajectory() -> list[Span]:
    now = datetime.now(UTC)
    root = _span(
        span_id="1" * 16,
        parent=None,
        type_=SpanType.PLANNER_STEP,
        attrs=PlannerStepAttributes(),
        start=now,
    )
    rh = root.provenance.content_hash
    model = _span(
        span_id="2" * 16,
        parent=root.span_id,
        type_=SpanType.MODEL_CALL,
        attrs=ModelCallAttributes(model_name="m", provider="p"),
        start=now,
        parent_hash=rh,
    )
    side = _span(
        span_id="3" * 16,
        parent=root.span_id,
        type_=SpanType.SIDE_EFFECT,
        attrs=SideEffectAttributes(action_type="x", target_system="y", success=False),
        start=now,
        status=SpanStatus.ERROR,
        error=ErrorInfo(error_type="E", message="boom"),
        parent_hash=rh,
    )
    return [root, model, side]


@pytest_asyncio.fixture
async def reader_token(session):  # type: ignore[no-untyped-def]
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="reader",
        end_customer_id=CUSTOMER,
        label="stats",
        created_by="pytest",
    )
    await session.commit()
    return f"{tid}.{sec}"


@pytest_asyncio.fixture
async def ingest_token(session):  # type: ignore[no-untyped-def]
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id=CUSTOMER,
        label="stats",
        created_by="pytest",
    )
    await session.commit()
    return f"{tid}.{sec}"


@pytest.fixture
def client(session):  # type: ignore[no-untyped-def]
    app = create_app()

    async def _override():  # type: ignore[no-untyped-def]
        async with get_session_factory()() as s:
            yield s

    app.dependency_overrides[session_dependency] = _override
    return TestClient(app)


def test_stats_aggregates_scoped(client, ingest_token, reader_token) -> None:  # type: ignore[no-untyped-def]
    spans = _trajectory()
    body = {"spans": [json.loads(s.model_dump_json(exclude_none=False)) for s in spans]}
    r = client.post("/spans", json=body, headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 3

    s = client.get("/stats", headers={"Authorization": f"Bearer {reader_token}"})
    assert s.status_code == 200, s.text
    data = s.json()
    assert data["span_count"] == 3
    assert data["trajectory_count"] == 1
    assert data["by_status"] == {"ok": 2, "error": 1}
    assert data["by_type"] == {"planner_step": 1, "model_call": 1, "side_effect": 1}
    # dashboard aggregates (P0)
    assert data["trajectory_volume"] == 1
    assert data["by_disposition"] == {"clean": 1, "warn": 0, "violation": 0}
    assert data["violation_count"] == 0
    assert data["coverage_by_regime"] == {"ECOA": 1}
    # controls (default catalog): this trajectory is ECOA + model_call, no policy_check/human_approval
    controls = {c["key"]: c for c in data["controls"]}
    assert controls["adverse_action"]["total"] == 1
    assert controls["adverse_action"]["passing"] == 0
    assert controls["adverse_action"]["status"] == "attention"
    assert controls["model_rationale"]["total"] == 1
    assert controls["model_rationale"]["passing"] == 0
    assert controls["human_review"]["status"] == "attention"
    assert controls["consumer_report"]["total"] == 0
    assert controls["consumer_report"]["status"] == "pass"


def test_stats_counts_violations(client, ingest_token, reader_token) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    root = _span(span_id="1" * 16, parent=None, type_=SpanType.PLANNER_STEP, attrs=PlannerStepAttributes(), start=now)
    pc = _span(
        span_id="2" * 16,
        parent=root.span_id,
        type_=SpanType.POLICY_CHECK,
        attrs=PolicyCheckAttributes(policy_name="ecoa.protected_class", policy_version="v1", result="fail"),
        start=now,
        parent_hash=root.provenance.content_hash,
    )
    body = {"spans": [json.loads(s.model_dump_json(exclude_none=False)) for s in (root, pc)]}
    assert client.post("/spans", json=body, headers={"Authorization": f"Bearer {ingest_token}"}).status_code == 202
    data = client.get("/stats", headers={"Authorization": f"Bearer {reader_token}"}).json()
    assert data["trajectory_volume"] == 1
    assert data["violation_count"] == 1
    assert data["by_disposition"]["violation"] == 1
    # ECOA + policy_check present → adverse_action control passes
    passed = {c["key"]: c for c in data["controls"]}
    assert passed["adverse_action"]["passing"] == 1
    assert passed["adverse_action"]["total"] == 1
    assert passed["adverse_action"]["status"] == "pass"


# --- P7: time-windowed metrics + previous period ---------------------------


def _ingest_at(client, ingest_token, *, start, regime, pc_result=None):  # type: ignore[no-untyped-def]
    """Ingest a one-trajectory window fixture: a root at `start` (+ optional policy_check)."""
    tid = uuid.uuid4().hex
    root = make_span(trajectory_id=tid, started_at=start, regime=[regime])
    spans = [root]
    if pc_result is not None:
        spans.append(
            make_span(
                trajectory_id=tid,
                started_at=start,
                regime=[regime],
                parent_span_id=root.span_id,
                parent_content_hash=root.provenance.content_hash,
                span_type=SpanType.POLICY_CHECK,
                attributes=PolicyCheckAttributes(policy_name="p", policy_version="v1", result=pc_result),
            )
        )
    r = client.post("/spans", json=envelope(spans), headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202, r.text
    return tid


def test_stats_no_window_is_v1_1_shape(client, reader_token) -> None:  # type: ignore[no-untyped-def]
    data = client.get("/stats", headers={"Authorization": f"Bearer {reader_token}"}).json()
    assert "window" not in data  # backward-compat: no window/previous when unwindowed
    assert "previous" not in data


def test_stats_windowed_with_previous(client, ingest_token, reader_token) -> None:  # type: ignore[no-untyped-def]
    from agent_capture.schema import RegulatoryRegime

    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    # current window [t0, t0+1h): one ECOA violation
    _ingest_at(client, ingest_token, start=t0 + timedelta(minutes=30), regime=RegulatoryRegime.ECOA, pc_result="fail")
    # previous window [t0-1h, t0): one FCRA clean
    _ingest_at(client, ingest_token, start=t0 - timedelta(minutes=30), regime=RegulatoryRegime.FCRA, pc_result=None)
    # outside both windows → counts in neither
    _ingest_at(client, ingest_token, start=t0 - timedelta(hours=3), regime=RegulatoryRegime.ECOA, pc_result="warn")

    params = {"from": t0.isoformat(), "to": (t0 + timedelta(hours=1)).isoformat()}
    data = client.get("/stats", params=params, headers={"Authorization": f"Bearer {reader_token}"}).json()
    assert data["window"] is None  # explicit from/to → no relative label
    assert data["trajectory_volume"] == 1
    assert data["violation_count"] == 1
    assert data["by_disposition"] == {"clean": 0, "warn": 0, "violation": 1}
    assert data["coverage_by_regime"] == {"ECOA": 1}

    prev = data["previous"]
    assert prev["trajectory_volume"] == 1
    assert prev["violation_count"] == 0
    assert prev["by_disposition"] == {"clean": 1, "warn": 0, "violation": 0}
    assert prev["coverage_by_regime"] == {"FCRA": 1}
    assert "controls" not in prev  # previous carries count-style aggregates only


def test_stats_empty_window_zeros(client, reader_token) -> None:  # type: ignore[no-untyped-def]
    t0 = datetime(2030, 1, 1, tzinfo=UTC)  # future window, no data
    params = {"from": t0.isoformat(), "to": (t0 + timedelta(days=1)).isoformat()}
    data = client.get("/stats", params=params, headers={"Authorization": f"Bearer {reader_token}"}).json()
    assert data["trajectory_volume"] == 0
    assert data["violation_count"] == 0
    assert data["by_disposition"] == {"clean": 0, "warn": 0, "violation": 0}
    assert data["coverage_by_regime"] == {}
    assert data["previous"]["trajectory_volume"] == 0


def test_stats_relative_window(client, ingest_token, reader_token) -> None:  # type: ignore[no-untyped-def]
    from agent_capture.schema import RegulatoryRegime

    now = datetime.now(UTC)
    _ingest_at(client, ingest_token, start=now - timedelta(days=1), regime=RegulatoryRegime.ECOA, pc_result="fail")
    _ingest_at(client, ingest_token, start=now - timedelta(days=20), regime=RegulatoryRegime.ECOA, pc_result=None)

    data = client.get("/stats", params={"window": "7d"}, headers={"Authorization": f"Bearer {reader_token}"}).json()
    assert data["window"] == "7d"
    assert data["trajectory_volume"] == 1  # only the day-old one
    assert data["violation_count"] == 1
    assert data["previous"]["trajectory_volume"] == 0  # 20d-old is outside the previous window too


def test_stats_invalid_window_400(client, reader_token) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/stats", params={"window": "banana"}, headers={"Authorization": f"Bearer {reader_token}"})
    assert r.status_code == 400
    assert r.json()["code"] == "LE007"


def test_stats_from_after_to_400(client, reader_token) -> None:  # type: ignore[no-untyped-def]
    params = {"from": "2026-06-02T00:00:00Z", "to": "2026-06-01T00:00:00Z"}
    r = client.get("/stats", params=params, headers={"Authorization": f"Bearer {reader_token}"})
    assert r.status_code == 400
    assert r.json()["code"] == "LE007"
