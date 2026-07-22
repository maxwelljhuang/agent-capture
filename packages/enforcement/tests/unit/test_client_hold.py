"""EnforcementClient fail-to-human polling (no DB; MockTransport)."""

from __future__ import annotations

import httpx
from agent_capture.enforcement import GateRequest
from agent_capture.schema import SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import SideEffectAttributes

from agent_capture_enforcement.client import EnforcementClient


def _req() -> GateRequest:
    return GateRequest(
        span_type=SpanType.SIDE_EFFECT,
        name="wire",
        trajectory_id="a" * 32,
        span_id="1" * 16,
        parent_span_id=None,
        attributes=SideEffectAttributes(action_type="payment.wire", target_system="bank", success=True),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
    )


class _Svc:
    """Returns hold on /verdict, then a scripted sequence of resolution statuses."""

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = statuses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/verdict":
            return httpx.Response(
                200, json={"decision": "hold", "hold_id": "h1", "policy_version": "v1", "rule_id": "r"}
            )
        if request.url.path == "/holds/h1/resolution":
            status = self._statuses.pop(0) if self._statuses else "pending"
            decision = {"approved": "allow", "rejected": "block"}.get(status)
            return httpx.Response(200, json={"hold_id": "h1", "status": status, "decision": decision})
        return httpx.Response(404)


def _client(svc: _Svc, *, max_hold_wait_s: int = 5) -> EnforcementClient:
    http = httpx.Client(transport=httpx.MockTransport(svc), base_url="http://svc")
    return EnforcementClient(client=http, hold_poll_interval_ms=10, max_hold_wait_s=max_hold_wait_s)


def test_hold_approved_resolves_to_allow() -> None:
    verdict = _client(_Svc(["pending", "approved"])).evaluate(_req())
    assert verdict.decision == "allow"
    assert verdict.hold_id == "h1"


def test_hold_rejected_resolves_to_block() -> None:
    verdict = _client(_Svc(["rejected"])).evaluate(_req())
    assert verdict.decision == "block"


def test_hold_wait_exceeded_aborts_to_block() -> None:
    # always pending -> abort after the wait budget (fail-safe for irreversible)
    verdict = _client(_Svc([]), max_hold_wait_s=1).evaluate(_req())
    assert verdict.decision == "block"
    assert "exceeded" in verdict.reason
