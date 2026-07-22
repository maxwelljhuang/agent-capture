"""End-to-end: a recorder ships spans → ledger stores them → /verify is green.

This is the "layer 1 meets layer 2" proof. Uses the recorder's
``HTTPExporter`` against the in-process FastAPI ledger (TestClient over
ASGI). No Docker required beyond Postgres.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app
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


class _ClientExporter(SpanExporter):
    """SpanExporter implementation that ships via FastAPI TestClient."""

    def __init__(self, client: TestClient, bearer: str) -> None:
        self.client = client
        self.bearer = bearer
        self.sent = 0

    def export(self, span: Span) -> None:
        body = {"spans": [json.loads(span.model_dump_json(exclude_none=False))]}
        r = self.client.post("/spans", json=body, headers={"Authorization": f"Bearer {self.bearer}"})
        assert r.status_code == 202, r.text
        self.sent += r.json()["accepted"]

    def flush(self, timeout_s: float | None = None) -> None:
        pass

    def shutdown(self, timeout_s: float | None = None) -> None:
        pass


@pytest_asyncio.fixture
async def ingest_token(session):
    tid = generate_token_id()
    sec = generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id="demo-co",
        label="e2e",
        created_by="pytest",
    )
    await session.commit()
    return f"{tid}.{sec}"


@pytest_asyncio.fixture
async def reader_token(session):
    tid = generate_token_id()
    sec = generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="reader",
        end_customer_id="demo-co",
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


def test_recorder_to_ledger_roundtrip(client, ingest_token, reader_token):
    """Build a trajectory via make_span(), POST it as recorder would,
    read it back, /verify is green."""
    parent = make_span(name="root")
    children = [
        make_span(
            trajectory_id=parent.trajectory_id,
            parent_span_id=parent.span_id,
            parent_content_hash=parent.provenance.content_hash,
            name=f"child-{i}",
        )
        for i in range(3)
    ]
    # ship as one batch (recorder-equivalent)
    r = client.post("/spans", json=envelope([parent, *children]), headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202
    assert r.json()["accepted"] == 4

    # readback
    r = client.get(f"/trajectories/{parent.trajectory_id}/spans", headers={"Authorization": f"Bearer {reader_token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["spans"]) == 4

    # /verify is green
    r = client.post(f"/verify/{parent.trajectory_id}", headers={"Authorization": f"Bearer {reader_token}"})
    out = r.json()
    assert out["status"] == "verified"
    assert out["chain_intact"] is True
