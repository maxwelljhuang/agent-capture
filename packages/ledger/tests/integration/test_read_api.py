"""GET /trajectories, /spans, /verify and access log entries."""

from __future__ import annotations

import pytest
import pytest_asyncio
from agent_capture.schema import SpanType
from agent_capture.schema.types import PolicyCheckAttributes
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.storage import models
from agent_capture_ledger.storage.engine import (
    get_session_factory,
    session_dependency,
)
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
)
from tests._helpers import envelope, make_span

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def tokens(session):
    """Seed an ingest + reader token for demo-co and a reader token for acme."""

    async def _seed(role: str, customer: str | None) -> str:
        tid = generate_token_id()
        sec = generate_secret()
        await TokenRepo(session).create(
            token_id=tid,
            token_hash=hash_secret(sec),
            role=role,
            end_customer_id=customer,
            label=f"{role}-{customer}",
            created_by="pytest",
        )
        return f"{tid}.{sec}"

    bag = {
        "ingest_demo": await _seed("ingest", "demo-co"),
        "reader_demo": await _seed("reader", "demo-co"),
        "reader_acme": await _seed("reader", "acme"),
        "admin": await _seed("admin", None),
    }
    await session.commit()
    return bag


@pytest.fixture
def client(session):
    app = create_app()

    async def _override():
        async with get_session_factory()() as s:
            yield s

    app.dependency_overrides[session_dependency] = _override
    return TestClient(app)


def _ingest_traj(client, bearer, n=4):
    parent = make_span()
    children = [
        make_span(
            trajectory_id=parent.trajectory_id,
            parent_span_id=parent.span_id,
            parent_content_hash=parent.provenance.content_hash,
        )
        for _ in range(n - 1)
    ]
    r = client.post("/spans", json=envelope([parent, *children]), headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 202, r.text
    return parent.trajectory_id


def test_get_trajectory_lists_spans(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"], n=3)
    r = client.get(f"/trajectories/{tid}/spans", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["trajectory_id"] == tid
    assert len(body["spans"]) == 3


def test_reader_cannot_cross_tenant(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"])
    r = client.get(f"/trajectories/{tid}", headers={"Authorization": f"Bearer {tokens['reader_acme']}"})
    assert r.status_code == 403
    assert r.json()["code"] == "LE104"


def test_admin_can_cross_tenant(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"])
    r = client.get(f"/trajectories/{tid}", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert r.status_code == 200


def test_list_trajectories_pagination(client, tokens):
    tids = {_ingest_traj(client, tokens["ingest_demo"], n=1) for _ in range(6)}
    r = client.get("/trajectories?limit=4", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    body = r.json()
    assert len(body["items"]) == 4
    cursor = body["next_cursor"]
    assert cursor

    r2 = client.get(
        f"/trajectories?limit=4&cursor={cursor}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"}
    )
    body2 = r2.json()
    assert len(body2["items"]) >= 1
    seen = {it["trajectory_id"] for it in body["items"] + body2["items"]}
    assert tids.issubset(seen)


def test_verify_returns_verified_for_clean(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"], n=4)
    r = client.post(f"/verify/{tid}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    body = r.json()
    assert body["status"] == "verified"
    assert body["chain_intact"] is True
    assert body["spans"] == 4
    assert body["findings"] == []


@pytest.mark.asyncio
async def test_every_read_writes_access_log(client, tokens, session):
    tid = _ingest_traj(client, tokens["ingest_demo"])
    client.get(f"/trajectories/{tid}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    client.get(f"/trajectories/{tid}/spans", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    client.post(f"/verify/{tid}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})

    # Fresh session — the test client used its own connection
    async with get_session_factory()() as s:
        rows = (await s.execute(select(models.AccessLog).where(models.AccessLog.target_id == tid))).scalars().all()
    actions = {r.action for r in rows}
    assert {"read.trajectory", "read.trajectory.spans", "verify.trajectory"} <= actions


@pytest.mark.asyncio
async def test_expired_token_is_rejected(client, session):
    """A token past its expires_at is refused even though it isn't revoked."""
    from datetime import UTC, datetime, timedelta

    tid = generate_token_id()
    sec = generate_secret()
    session.add(
        models.ApiToken(
            token_id=tid,
            token_hash=hash_secret(sec),
            role="reader",
            end_customer_id="demo-co",
            label="expired-reader",
            created_by="pytest",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await session.commit()

    r = client.get("/trajectories", headers={"Authorization": f"Bearer {tid}.{sec}"})
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_unscoped_ingest_token_accepts_any_tenant(client, tokens, session):
    """An unscoped (null-customer) ingest token may ship spans for ANY tenant (#63)."""
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id=None,  # unscoped
        label="unscoped",
        created_by="pytest",
    )
    await session.commit()
    bearer = {"Authorization": f"Bearer {tid}.{sec}"}

    demo = make_span(end_customer_id="demo-co")
    acme = make_span(end_customer_id="acme")
    for s in (demo, acme):
        r = client.post("/spans", json=envelope([s]), headers=bearer)
        assert r.status_code == 202, r.text
        assert r.json()["accepted"] == 1

    # each landed under its OWN tenant (admin reads cross-tenant)
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    assert client.get(f"/trajectories/{demo.trajectory_id}", headers=admin).json()["end_customer_id"] == "demo-co"
    assert client.get(f"/trajectories/{acme.trajectory_id}", headers=admin).json()["end_customer_id"] == "acme"


def _ingest_disposition_traj(client, bearer, result):
    root = make_span()
    spans = [root]
    if result is not None:
        spans.append(
            make_span(
                trajectory_id=root.trajectory_id,
                parent_span_id=root.span_id,
                parent_content_hash=root.provenance.content_hash,
                span_type=SpanType.POLICY_CHECK,
                attributes=PolicyCheckAttributes(
                    policy_name="ecoa.protected_class", policy_version="v1", result=result
                ),
            )
        )
    r = client.post("/spans", json=envelope(spans), headers={"Authorization": f"Bearer {bearer}"})
    assert r.status_code == 202, r.text
    return root.trajectory_id


@pytest.mark.parametrize(
    ("result", "expected"),
    [("fail", "violation"), ("warn", "warn"), ("pass", "clean"), ("not_applicable", "clean"), (None, "clean")],
)
def test_trajectory_disposition(client, tokens, result, expected):
    tid = _ingest_disposition_traj(client, tokens["ingest_demo"], result)
    r = client.get(f"/trajectories/{tid}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    assert r.status_code == 200, r.text
    assert r.json()["disposition"] == expected


def test_list_row_has_disposition_and_aggregated_regime(client, tokens):
    from agent_capture.schema import RegulatoryRegime

    root = make_span(regime=[RegulatoryRegime.ECOA])
    pc = make_span(
        trajectory_id=root.trajectory_id,
        parent_span_id=root.span_id,
        parent_content_hash=root.provenance.content_hash,
        regime=[RegulatoryRegime.FCRA],
        span_type=SpanType.POLICY_CHECK,
        attributes=PolicyCheckAttributes(policy_name="p", policy_version="v1", result="fail"),
    )
    client.post("/spans", json=envelope([root, pc]), headers={"Authorization": f"Bearer {tokens['ingest_demo']}"})
    r = client.get("/trajectories", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    row = next(it for it in r.json()["items"] if it["trajectory_id"] == root.trajectory_id)
    assert row["disposition"] == "violation"
    assert set(row["regulatory_regime"]) == {"ECOA", "FCRA"}


def test_admin_customer_filter(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"], n=1)  # demo-co
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    seen_demo = client.get("/trajectories?end_customer_id=demo-co", headers=admin).json()["items"]
    assert any(it["trajectory_id"] == tid for it in seen_demo)
    seen_other = client.get("/trajectories?end_customer_id=other-bank", headers=admin).json()["items"]
    assert all(it["trajectory_id"] != tid for it in seen_other)
    # a reader cannot widen scope via the param — still pinned to its own tenant
    reader = {"Authorization": f"Bearer {tokens['reader_demo']}"}
    via_reader = client.get("/trajectories?end_customer_id=other-bank", headers=reader).json()["items"]
    assert any(it["trajectory_id"] == tid for it in via_reader)


def test_access_log_endpoint(client, tokens):
    tid = _ingest_traj(client, tokens["ingest_demo"])
    reader = {"Authorization": f"Bearer {tokens['reader_demo']}"}
    client.get(f"/trajectories/{tid}", headers=reader)  # generates a read.trajectory entry
    items = client.get("/access-log", headers=reader).json()["items"]
    assert "read.trajectory" in {it["action"] for it in items}
    assert all(it["end_customer_id"] == "demo-co" for it in items)  # tenant-scoped


def test_list_row_subject_ref_only_for_fingerprint(client, tokens):
    ingest = {"Authorization": f"Bearer {tokens['ingest_demo']}"}
    fp_root = make_span(subject_id="[FP:abc123:subject_id]")
    raw_root = make_span(subject_id="APP-10293")  # raw → must NOT be surfaced
    none_root = make_span(subject_id=None)
    for s in (fp_root, raw_root, none_root):
        assert client.post("/spans", json=envelope([s]), headers=ingest).status_code == 202
    items = client.get("/trajectories", headers={"Authorization": f"Bearer {tokens['reader_demo']}"}).json()["items"]
    by_tid = {it["trajectory_id"]: it for it in items}
    assert by_tid[fp_root.trajectory_id]["subject_ref"] == "[FP:abc123:subject_id]"
    assert by_tid[raw_root.trajectory_id]["subject_ref"] is None
    assert by_tid[none_root.trajectory_id]["subject_ref"] is None


def test_disposition_fail_beats_warn(client, tokens):
    root = make_span()
    common = dict(
        trajectory_id=root.trajectory_id,
        parent_span_id=root.span_id,
        parent_content_hash=root.provenance.content_hash,
        span_type=SpanType.POLICY_CHECK,
    )
    warn = make_span(**common, attributes=PolicyCheckAttributes(policy_name="p", policy_version="v1", result="warn"))
    fail = make_span(**common, attributes=PolicyCheckAttributes(policy_name="p2", policy_version="v1", result="fail"))
    r = client.post(
        "/spans", json=envelope([root, warn, fail]), headers={"Authorization": f"Bearer {tokens['ingest_demo']}"}
    )
    assert r.status_code == 202, r.text
    d = client.get(f"/trajectories/{root.trajectory_id}", headers={"Authorization": f"Bearer {tokens['reader_demo']}"})
    assert d.json()["disposition"] == "violation"
