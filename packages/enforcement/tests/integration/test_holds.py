"""Hold queue + review API, backed by SQLite (a faithful double for the plain
hold table). Covers the repository lifecycle, the reviewer/resolution API, and
reviewer auth.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import SideEffectAttributes
from fastapi.testclient import TestClient

import agent_capture_enforcement.storage.engine as eng
from agent_capture_enforcement.config import Settings, set_settings
from agent_capture_enforcement.service.app import create_app
from agent_capture_enforcement.storage.engine import get_session_factory, init_db
from agent_capture_enforcement.storage.repository import HoldRepo

pytestmark = pytest.mark.integration

_HOLD_RULE = (
    "version: enf-v1\nrules:\n"
    "  - id: review_wire\n    span_type: side_effect\n"
    "    evaluator: action_type_allowed\n    params: { deny: [payment.wire] }\n"
    "    mode: blocking\n    failure_mode: fail_to_human\n"
)


@pytest.fixture
def dsn(tmp_path: Path) -> Iterator[str]:
    url = f"sqlite+aiosqlite:///{tmp_path}/holds.db"
    eng._engine = None
    eng._factory = None
    set_settings(Settings(database_url=url, rules_path=None))
    yield url
    eng._engine = None
    eng._factory = None
    set_settings(None)


def _payload() -> dict[str, Any]:
    attrs = SideEffectAttributes(action_type="payment.wire", target_system="bank", success=True)
    comp = ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )
    return {
        "span_type": "side_effect",
        "name": "wire",
        "trajectory_id": "a" * 32,
        "span_id": "1" * 16,
        "parent_span_id": None,
        "attributes": attrs.model_dump(mode="json"),
        "compliance": comp.model_dump(mode="json"),
    }


async def test_repo_lifecycle(dsn: str) -> None:
    await init_db()
    factory = get_session_factory()
    async with factory() as s:
        repo = HoldRepo(s)
        hold = await repo.create(
            end_customer_id="acme",
            trajectory_id="a" * 32,
            span_id="1" * 16,
            policy_name="enforcement",
            policy_version="v1",
            rule_id="review_wire",
        )
        await s.commit()
        hid = hold.hold_id
        assert (await repo.list_pending("acme"))[0].hold_id == hid
        resolved = await repo.resolve(hid, decision="approved", decision_reason="ok")
        await s.commit()
        assert resolved is not None
        assert resolved.status == "approved"
        # resolving an already-terminal hold is a no-op
        assert await repo.resolve(hid, decision="rejected") is None


async def test_repo_expire_due(dsn: str) -> None:
    await init_db()
    factory = get_session_factory()
    async with factory() as s:
        repo = HoldRepo(s)
        await repo.create(
            end_customer_id="acme",
            trajectory_id="a" * 32,
            span_id="2" * 16,
            policy_name="enforcement",
            policy_version="v1",
            rule_id="r",
            hold_timeout_s=0,  # already expired
        )
        await s.commit()
    async with factory() as s:
        count = await HoldRepo(s).expire_due()
        await s.commit()
        assert count == 1


def test_verdict_creates_hold_and_review_flow(dsn: str, tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_HOLD_RULE, encoding="utf-8")
    set_settings(Settings(database_url=dsn, rules_path=rules))

    with TestClient(create_app()) as client:  # lifespan runs init_db in the serving loop
        verdict = client.post("/verdict", json=_payload()).json()
        assert verdict["decision"] == "hold"
        hold_id = verdict["hold_id"]
        assert hold_id

        pending = client.get("/holds", params={"end_customer_id": "acme"}).json()
        assert [h["hold_id"] for h in pending] == [hold_id]

        # before resolution, the recorder's poll sees 'pending'
        assert client.get(f"/holds/{hold_id}/resolution").json()["status"] == "pending"

        resolve = client.post(f"/holds/{hold_id}/resolve", json={"decision": "approved", "decision_reason": "ok"})
        assert resolve.status_code == 200

        resolution = client.get(f"/holds/{hold_id}/resolution").json()
        assert resolution["status"] == "approved"
        assert resolution["decision"] == "allow"


def test_reviewer_token_required(dsn: str) -> None:
    set_settings(Settings(database_url=dsn, reviewer_token="rv"))
    with TestClient(create_app()) as client:
        assert client.get("/holds", params={"end_customer_id": "acme"}).status_code == 401
        ok = client.get("/holds", params={"end_customer_id": "acme"}, headers={"Authorization": "Bearer rv"})
        assert ok.status_code == 200
        assert ok.json() == []


def test_holds_count(dsn: str, tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_HOLD_RULE, encoding="utf-8")
    set_settings(Settings(database_url=dsn, rules_path=rules))
    with TestClient(create_app()) as client:
        assert client.get("/holds/count", params={"end_customer_id": "acme"}).json() == {"pending": 0}
        client.post("/verdict", json=_payload())  # creates one acme hold
        assert client.get("/holds/count", params={"end_customer_id": "acme"}).json() == {"pending": 1}


def test_holds_count_reviewer_scoped(dsn: str) -> None:
    set_settings(Settings(database_url=dsn, reviewer_token="rv", reviewer_customer="acme"))
    with TestClient(create_app()) as client:
        h = {"Authorization": "Bearer rv"}
        assert client.get("/holds/count", params={"end_customer_id": "acme"}, headers=h).status_code == 200
        assert client.get("/holds/count", params={"end_customer_id": "other"}, headers=h).status_code == 403


def test_reviewer_scoped_list_rejects_other_tenant(dsn: str) -> None:
    set_settings(Settings(database_url=dsn, reviewer_token="rv", reviewer_customer="acme"))
    with TestClient(create_app()) as client:
        h = {"Authorization": "Bearer rv"}
        # bound to acme: its own tenant is allowed, another tenant is forbidden
        assert client.get("/holds", params={"end_customer_id": "acme"}, headers=h).status_code == 200
        assert client.get("/holds", params={"end_customer_id": "other"}, headers=h).status_code == 403


def test_reviewer_cannot_resolve_other_tenant_hold(dsn: str, tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_HOLD_RULE, encoding="utf-8")
    set_settings(Settings(database_url=dsn, rules_path=rules, reviewer_token="rv", reviewer_customer="acme"))

    with TestClient(create_app()) as client:
        h = {"Authorization": "Bearer rv"}
        # a hold belonging to tenant "other"
        other = _payload()
        other["compliance"]["end_customer_id"] = "other"
        hold_id = client.post("/verdict", json=other).json()["hold_id"]
        assert hold_id

        # the acme-bound reviewer cannot resolve another tenant's hold
        r = client.post(f"/holds/{hold_id}/resolve", json={"decision": "approved"}, headers=h)
        assert r.status_code == 403
