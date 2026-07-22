"""Attestation build + sign + tamper-detection.

The whole point: an attacker who rewrites ``spans.body`` after the fact
should be caught because the trajectory's recomputed root no longer
matches the leaf in the signed attestation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.integrity import merkle
from agent_capture_ledger.integrity.attestation import (
    build_window,
    proof_for,
    signing_payload,
)
from agent_capture_ledger.integrity.signer import (
    FileEd25519Signer,
    generate_keypair,
    verify_signature,
)
from agent_capture_ledger.storage.engine import (
    get_session_factory,
    session_dependency,
)
from agent_capture_ledger.storage.repository import SpanRepo, TokenRepo
from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
)
from tests._helpers import envelope, make_span

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def signer(tmp_path):
    priv_path, pub_path = generate_keypair(tmp_path, "test-2026")
    return FileEd25519Signer(priv_path, "test-2026"), pub_path


@pytest_asyncio.fixture
async def ingest_token(session):
    tid = generate_token_id()
    sec = generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id="demo-co",
        label="test",
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


@pytest.mark.asyncio
async def test_window_signs_and_proof_verifies(client, ingest_token, signer, migrated_dsn):
    s, pub_path = signer
    parent = make_span()
    child = make_span(
        trajectory_id=parent.trajectory_id,
        parent_span_id=parent.span_id,
        parent_content_hash=parent.provenance.content_hash,
    )
    r = client.post("/spans", json=envelope([parent, child]), headers={"Authorization": f"Bearer {ingest_token}"})
    assert r.status_code == 202

    now = datetime.now(UTC) + timedelta(minutes=1)
    factory = get_session_factory()
    async with factory() as sess:
        result = await build_window(
            sess,
            end_customer_id="demo-co",
            window_start=now - timedelta(hours=1),
            window_end=now,
            signer=s,
        )
        await sess.commit()
    assert result is not None
    assert result.trajectory_count == 1

    # Signature verifies
    payload = signing_payload(root=result.merkle_root, window_end=result.window_end, end_customer_id="demo-co")
    assert verify_signature(pub_path.read_bytes(), payload, result.signature)

    # Proof is well-formed and verifies
    async with factory() as sess:
        proof = await proof_for(sess, trajectory_id=parent.trajectory_id)
    assert proof is not None
    assert proof["merkle_root"] == result.merkle_root


@pytest.mark.asyncio
async def test_tampered_body_breaks_attestation(client, ingest_token, signer, migrated_dsn):
    """Rewrite spans.body via the retention role; recomputed root must diverge from leaf."""
    s, _pub_path = signer
    parent = make_span(name="original")
    child = make_span(
        trajectory_id=parent.trajectory_id,
        parent_span_id=parent.span_id,
        parent_content_hash=parent.provenance.content_hash,
    )
    client.post("/spans", json=envelope([parent, child]), headers={"Authorization": f"Bearer {ingest_token}"})

    now = datetime.now(UTC) + timedelta(minutes=1)
    factory = get_session_factory()
    async with factory() as sess:
        result = await build_window(
            sess,
            end_customer_id="demo-co",
            window_start=now - timedelta(hours=1),
            window_end=now,
            signer=s,
        )
        await sess.commit()
    assert result is not None
    original_leaf = next(rh for tid, rh in result.leaf_pairs if tid == parent.trajectory_id)

    # Tamper via the retention role (only role that can mutate spans).
    bypass = create_async_engine(
        migrated_dsn,
        isolation_level="AUTOCOMMIT",
        connect_args={"server_settings": {"role": "ledger_retention"}},
    )
    async with bypass.connect() as conn:
        await conn.execute(
            text(
                "UPDATE spans SET body = jsonb_set(body, '{name}', '\"TAMPERED\"') "
                f"WHERE span_id = '{parent.span_id}'"
            )
        )
    await bypass.dispose()

    # Recompute the trajectory root from current DB state.
    async with factory() as sess:
        spans = await SpanRepo(sess).fetch_trajectory(parent.trajectory_id)

    # We need the *recomputed* content_hashes (not the stored ones — those
    # weren't updated; tampering didn't refresh content_hash). Then build
    # the trajectory root.
    from agent_capture.schema import Span as ACSpan
    from agent_capture.schema.canonical import content_hash

    recomputed = [
        content_hash(ACSpan.model_validate(s.body)) for s in sorted(spans, key=lambda x: (x.start_time, x.span_id))
    ]
    recomputed_root = merkle.trajectory_root(recomputed)

    # The recomputed root must NOT match the original leaf in the attestation.
    assert recomputed_root != original_leaf
