"""Advisory enforcement (layer 5) fires through the real ingest path.

Proves the seam wired in ingest.py: when a rule file is configured AND the
enforcement engine is installed, accepted gated spans are evaluated advisorily
(a verdict is produced + counted) while ingest still returns 202. Skipped in
the standalone ledger venv (no enforcement package).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

pytest.importorskip("agent_capture_enforcement")

from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import SideEffectAttributes
from agent_capture_enforcement.observability import advisory_verdicts
from fastapi.testclient import TestClient

from agent_capture_ledger.api.app import create_app
from agent_capture_ledger.config import Settings, set_settings
from agent_capture_ledger.storage.engine import get_session_factory, session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import generate_secret, generate_token_id, hash_secret

pytestmark = pytest.mark.integration

CUSTOMER = "demo-co"


def _side_effect_span() -> Span:
    now = datetime.now(UTC)
    placeholder = Span(
        span_id="1" * 16,
        parent_span_id=None,
        trajectory_id="b" * 32,
        name="wire_funds",
        type=SpanType.SIDE_EFFECT,
        start_time=now,
        end_time=now + timedelta(milliseconds=5),
        status=SpanStatus.OK,
        attributes=SideEffectAttributes(action_type="payment.wire", target_system="bank.api", success=True),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id=CUSTOMER,
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(content_hash="0" * 64, parent_content_hash=None),
    )
    return placeholder.model_copy(
        update={"provenance": ProvenanceFields(content_hash=content_hash(placeholder), parent_content_hash=None)}
    )


@pytest_asyncio.fixture
async def ingest_token(session):  # type: ignore[no-untyped-def]
    tid, sec = generate_token_id(), generate_secret()
    await TokenRepo(session).create(
        token_id=tid,
        token_hash=hash_secret(sec),
        role="ingest",
        end_customer_id=CUSTOMER,
        label="advisory-test",
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


def test_advisory_fires_at_ingest(client, ingest_token, migrated_dsn, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "version: enf-v1\nrules:\n"
        "  - id: deny_wire\n    span_type: side_effect\n"
        "    evaluator: action_type_allowed\n    params: { deny: [payment.wire] }\n",
        encoding="utf-8",
    )
    # Configure enforcement on top of the test DSN (same engine; only adds the path).
    set_settings(Settings(database_url=migrated_dsn, enforcement_rules_path=rules_file))

    span = _side_effect_span()
    before = advisory_verdicts.labels(result="fail", span_type="side_effect")._value.get()

    body = {"spans": [json.loads(span.model_dump_json(exclude_none=False))]}
    r = client.post("/spans", json=body, headers={"Authorization": f"Bearer {ingest_token}"})

    assert r.status_code == 202, r.text
    assert r.json()["accepted"] == 1  # ingest unaffected by advisory

    after = advisory_verdicts.labels(result="fail", span_type="side_effect")._value.get()
    assert after == before + 1  # the denylisted wire produced one advisory 'fail'
