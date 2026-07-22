"""End-to-end ingest throughput floor against real Postgres.

The floor is **200 spans/s through the full stack** (FastAPI TestClient
ASGI -> auth + Argon2 token verify -> Pydantic validation -> canonical
hash recompute -> Postgres insert). Realistic numbers we see by env:

- M-series MacBook: ~700-900 spans/s
- GitHub Actions ubuntu-latest (2 vCPU): ~400 spans/s
- ~200 spans/s would mean a 2x regression from the GHA baseline

The test is a regression sentinel, not a marketing benchmark. Sustained
production throughput claims belong with real load tests on the target
deployment shape, not a serialized TestClient loop.
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
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

pytestmark = [pytest.mark.integration, pytest.mark.perf]


@pytest_asyncio.fixture
async def ingest_token(session):
    tid = generate_token_id()
    sec = generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id="demo-co",
        label="perf",
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


_THROUGHPUT_FLOOR_SPANS_PER_SEC = 200


def test_ingest_throughput_above_floor(client, ingest_token):
    """5 batches x 100 spans must clear the regression floor."""
    batches = [envelope([make_span() for _ in range(100)]) for _ in range(5)]
    headers = {"Authorization": f"Bearer {ingest_token}"}

    start = time.perf_counter()
    accepted = 0
    for body in batches:
        r = client.post("/spans", json=body, headers=headers)
        assert r.status_code == 202
        accepted += r.json()["accepted"]
    elapsed = time.perf_counter() - start

    assert accepted == 500
    rate = accepted / elapsed
    assert rate >= _THROUGHPUT_FLOOR_SPANS_PER_SEC, (
        f"ingest rate {rate:.0f} spans/s below {_THROUGHPUT_FLOOR_SPANS_PER_SEC}/s floor"
    )
