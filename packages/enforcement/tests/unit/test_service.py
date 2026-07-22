"""Verdict service (FastAPI) tests via in-process TestClient."""

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

from agent_capture_enforcement.config import Settings, set_settings
from agent_capture_enforcement.service.app import create_app

_DENY_WIRE = (
    "version: enf-v1\nrules:\n"
    "  - id: deny_wire\n    span_type: side_effect\n"
    "    evaluator: action_type_allowed\n    params: { deny: [payment.wire] }\n"
    "    mode: blocking\n    failure_mode: fail_closed\n"
)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
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


def test_health() -> None:
    set_settings(Settings(rules_path=None))
    r = TestClient(create_app()).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_verdict_allow_without_rules() -> None:
    set_settings(Settings(rules_path=None))
    r = TestClient(create_app()).post("/verdict", json=_payload())
    assert r.status_code == 200
    assert r.json()["decision"] == "allow"


def test_verdict_block_with_rule(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_DENY_WIRE, encoding="utf-8")
    set_settings(Settings(rules_path=rules))
    r = TestClient(create_app()).post("/verdict", json=_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "block"
    assert body["rule_id"] == "deny_wire"
    assert body["policy_version"] == "enf-v1"


def test_service_token_required(tmp_path: Path) -> None:
    set_settings(Settings(rules_path=None, service_token="s3cret"))
    client = TestClient(create_app())
    assert client.post("/verdict", json=_payload()).status_code == 401
    ok = client.post("/verdict", json=_payload(), headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200
