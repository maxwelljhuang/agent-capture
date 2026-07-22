"""Retention enforcement against a real Postgres.

We test the slow path (row deletes) — the drop-partition fast path needs
spans dated into a past partition, which would mean creating an extra
partition just for this test. Row-delete coverage proves the logic and
the trigger-bypass-via-role mechanic.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.config import Settings, set_settings
from agent_capture_ledger.retention.enforcer import run_retention
from agent_capture_ledger.storage import models
from agent_capture_ledger.storage.engine import (
    get_session_factory,
    reset_engines,
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
    async def _seed(role, customer):
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


@pytest.mark.asyncio
async def test_transient_expires_via_row_delete(client, tokens, migrated_dsn):
    """A TRANSIENT span past TTL is deleted; STANDARD survives."""
    from agent_capture.schema.compliance import RetentionClass

    transient = make_span(retention=RetentionClass.TRANSIENT)
    standard = make_span(retention=RetentionClass.STANDARD)
    r = client.post(
        "/spans", json=envelope([transient, standard]), headers={"Authorization": f"Bearer {tokens['ingest_demo']}"}
    )
    assert r.status_code == 202

    # Age the transient span past TTL by connecting as ledger_retention.
    from sqlalchemy.ext.asyncio import create_async_engine

    bypass = create_async_engine(
        migrated_dsn,
        isolation_level="AUTOCOMMIT",
        connect_args={"server_settings": {"role": "ledger_retention"}},
    )
    async with bypass.connect() as conn:
        await conn.execute(
            text(f"UPDATE spans SET end_time = end_time - INTERVAL '90 days' WHERE span_id = '{transient.span_id}'")
        )
    await bypass.dispose()

    # Configure 7-day TTL for transient → it's now 90d old → expired.
    set_settings(
        Settings(
            database_url=migrated_dsn,
            retention_transient_days=7,
            retention_standard_days=90,
            retention_extended_days=2555,
        )
    )
    await reset_engines()

    # Build a retention engine that explicitly SET ROLE ledger_retention
    # so the append-only trigger lets us delete.
    eng = create_async_engine(
        migrated_dsn, isolation_level="AUTOCOMMIT", connect_args={"server_settings": {"role": "ledger_retention"}}
    )
    report = await run_retention(eng)
    await eng.dispose()

    assert report.rows_deleted.get("transient", 0) >= 1
    # standard not yet expired
    assert report.rows_deleted.get("standard", 0) == 0

    async with get_session_factory()() as s:
        survivors = (
            (
                await s.execute(
                    select(models.Span.span_id).where(
                        models.Span.trajectory_id.in_([transient.trajectory_id, standard.trajectory_id])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert transient.span_id not in survivors
        assert standard.span_id in survivors

        ops = (await s.execute(select(models.RetentionOperation))).scalars().all()
        assert any(op.spans_affected >= 1 for op in ops)


@pytest.mark.asyncio
async def test_litigation_hold_blocks_deletion(client, tokens, migrated_dsn):
    from agent_capture.schema.compliance import RetentionClass
    from sqlalchemy.ext.asyncio import create_async_engine

    held = make_span(retention=RetentionClass.TRANSIENT)
    r = client.post("/spans", json=envelope([held]), headers={"Authorization": f"Bearer {tokens['ingest_demo']}"})
    assert r.status_code == 202

    # Place a hold on this trajectory.
    r = client.post(
        f"/litigation-holds/{held.trajectory_id}",
        json={"reason": "discovery"},
        headers={"Authorization": f"Bearer {tokens['admin']}"},
    )
    assert r.status_code == 201

    # Age the span past TTL.
    bypass = create_async_engine(migrated_dsn, isolation_level="AUTOCOMMIT")
    async with bypass.connect() as conn:
        await conn.execute(text("SET ROLE ledger_retention"))
        await conn.execute(
            text(f"UPDATE spans SET end_time = end_time - INTERVAL '90 days' WHERE span_id = '{held.span_id}'")
        )
        await conn.execute(text("RESET ROLE"))
    await bypass.dispose()

    set_settings(Settings(database_url=migrated_dsn, retention_transient_days=7))
    await reset_engines()

    eng = create_async_engine(
        migrated_dsn, isolation_level="AUTOCOMMIT", connect_args={"server_settings": {"role": "ledger_retention"}}
    )
    report = await run_retention(eng)
    await eng.dispose()

    assert report.rows_deleted.get("transient", 0) == 0
    async with get_session_factory()() as s:
        survives = (
            await s.execute(select(models.Span.span_id).where(models.Span.span_id == held.span_id))
        ).scalar_one_or_none()
        assert survives == held.span_id
