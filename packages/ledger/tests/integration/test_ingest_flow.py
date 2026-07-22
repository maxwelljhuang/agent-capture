"""End-to-end ingest against a real Postgres.

These tests run the full ingest path: HTTP envelope → Pydantic validation
→ canonical hash recompute → idempotency check → bulk insert. Each test
hits a fresh truncated DB.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
)
from tests._helpers import envelope, make_span

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def ingest_token(session):
    """Create an ingest-role token scoped to demo-co; return the bearer."""
    token_id = generate_token_id()
    secret = generate_secret()
    await TokenRepo(session).create(
        token_id=token_id,
        token_hash=hash_secret(secret),
        role="ingest",
        end_customer_id="demo-co",
        label="test",
        created_by="pytest",
    )
    await session.commit()
    return f"{token_id}.{secret}"


@pytest_asyncio.fixture
async def reader_token(session):
    token_id = generate_token_id()
    secret = generate_secret()
    await TokenRepo(session).create(
        token_id=token_id,
        token_hash=hash_secret(secret),
        role="reader",
        end_customer_id="demo-co",
        label="test",
        created_by="pytest",
    )
    await session.commit()
    return f"{token_id}.{secret}"


@pytest.fixture
def client(session):
    # The TestClient drives the same app but with its own session dep
    # override so it shares the migrated DB.
    app = create_app()

    async def _override():
        from agent_capture_ledger.storage.engine import get_session_factory

        async with get_session_factory()() as s:
            yield s

    app.dependency_overrides[session_dependency] = _override
    return TestClient(app)


def test_happy_path_accepts_batch(client, ingest_token):
    spans = [make_span() for _ in range(5)]
    r = client.post("/spans", json=envelope(spans), headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 5
    assert body["rejected"] == []


def test_rejects_missing_auth(client):
    spans = [make_span()]
    r = client.post("/spans", json=envelope(spans))
    assert r.status_code == 401
    body = r.json()
    assert body.get("code") == "LE101"


def test_rejects_malformed_bearer(client):
    spans = [make_span()]
    r = client.post("/spans", json=envelope(spans), headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401
    assert r.json()["code"] == "LE102"


def test_reader_token_cannot_ingest(client, reader_token):
    spans = [make_span()]
    r = client.post("/spans", json=envelope(spans), headers={"Authorization": f"Bearer {reader_token}"})
    assert r.status_code == 403
    assert r.json()["code"] == "LE103"


def test_tenant_mismatch_quarantines(client, ingest_token):
    """Token is demo-co; span claims acme."""
    span = make_span(end_customer_id="acme")
    r = client.post("/spans", json=envelope([span]), headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["code"] == "LE002"


def test_corrupted_content_hash_quarantines(client, ingest_token):
    s = make_span()
    payload = s.model_dump(mode="json")
    payload["provenance"]["content_hash"] = "0" * 64  # lie
    r = client.post("/spans", json={"spans": [payload]}, headers={"Authorization": f"Bearer {ingest_token}"})
    body = r.json()
    assert body["accepted"] == 0
    assert body["rejected"][0]["code"] == "LE003"


def test_idempotency_dedupes_silent(client, ingest_token):
    s = make_span()
    headers = {"Authorization": f"Bearer {ingest_token}"}
    r1 = client.post("/spans", json=envelope([s]), headers=headers)
    r2 = client.post("/spans", json=envelope([s]), headers=headers)
    assert r1.json()["accepted"] == 1
    # Second time: silent ack — neither accepted nor rejected
    assert r2.json()["accepted"] == 0
    assert r2.json()["rejected"] == []


def test_immutability_violation_rejects(client, ingest_token):
    """Same (span_id, trajectory_id), different body → LE004."""
    s = make_span()
    headers = {"Authorization": f"Bearer {ingest_token}"}
    client.post("/spans", json=envelope([s]), headers=headers)

    # Build a span with same ids but a different name → different hash
    twin = make_span(span_id=s.span_id, trajectory_id=s.trajectory_id, name="different")
    r = client.post("/spans", json=envelope([twin]), headers=headers)
    body = r.json()
    assert body["accepted"] == 0
    assert body["rejected"][0]["code"] == "LE004"


def test_parent_hash_mismatch_quarantines(client, ingest_token):
    parent = make_span()
    bad_child = make_span(
        trajectory_id=parent.trajectory_id,
        parent_span_id=parent.span_id,
        parent_content_hash="f" * 64,  # wrong
    )
    headers = {"Authorization": f"Bearer {ingest_token}"}
    # ship parent first so child's lookup succeeds
    r1 = client.post("/spans", json=envelope([parent]), headers=headers)
    assert r1.json()["accepted"] == 1
    r2 = client.post("/spans", json=envelope([bad_child]), headers=headers)
    body = r2.json()
    assert body["rejected"][0]["code"] == "LE005"


def test_health_and_ready(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/version").status_code == 200
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "ledger_ingest_spans_total" in r.text
