"""EnforcementClient: happy path, outage fallback, and recorder gate wiring."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from agent_capture import traced
from agent_capture.enforcement import EnforcementBlocked, GateRequest, set_gate
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span, SpanStatus, SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import SideEffectAttributes
from fastapi.testclient import TestClient

from agent_capture_enforcement.client import EnforcementClient
from agent_capture_enforcement.config import Settings, set_settings
from agent_capture_enforcement.service.app import create_app

_DENY_WIRE = (
    "version: enf-v1\nrules:\n"
    "  - id: deny_wire\n    span_type: side_effect\n"
    "    evaluator: action_type_allowed\n    params: { deny: [payment.wire] }\n"
    "    mode: blocking\n    failure_mode: fail_closed\n"
)


def _comp() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _attrs() -> SideEffectAttributes:
    return SideEffectAttributes(action_type="payment.wire", target_system="bank", success=True)


def _req() -> GateRequest:
    return GateRequest(
        span_type=SpanType.SIDE_EFFECT,
        name="wire",
        trajectory_id="a" * 32,
        span_id="1" * 16,
        parent_span_id=None,
        attributes=_attrs(),
        compliance=_comp(),
    )


class _CaptureExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    set_settings(None)
    set_gate(None)


def _raising_transport() -> httpx.Client:
    def _boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("verdict service down")

    return httpx.Client(transport=httpx.MockTransport(_boom), base_url="http://svc")


# ---- client happy path ----------------------------------------------------


def test_client_allow_without_rules() -> None:
    set_settings(Settings(rules_path=None))
    client = EnforcementClient(client=TestClient(create_app()))
    assert client.evaluate(_req()).decision == "allow"


def test_client_block_with_rule(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_DENY_WIRE, encoding="utf-8")
    set_settings(Settings(rules_path=rules))
    client = EnforcementClient(client=TestClient(create_app()))
    verdict = client.evaluate(_req())
    assert verdict.decision == "block"
    assert verdict.rule_id == "deny_wire"


# ---- outage fallback ------------------------------------------------------


def test_client_fallback_allows_by_default() -> None:
    client = EnforcementClient(client=_raising_transport())
    verdict = client.evaluate(_req())
    assert verdict.decision == "allow"
    assert "fallback" in verdict.reason


def test_client_fallback_blocks_for_fail_closed_rule() -> None:
    client = EnforcementClient(
        client=_raising_transport(),
        fallback={("acme", "payment.wire"): "fail_closed"},
    )
    assert client.evaluate(_req()).decision == "block"


async def test_client_async_allow_without_rules() -> None:
    set_settings(Settings(rules_path=None))
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://svc") as ac:
        client = EnforcementClient(async_client=ac)
        verdict = await client.evaluate_async(_req())
    assert verdict.decision == "allow"


# ---- recorder gate wiring (Phase 0 + Phase 2 together) --------------------


def _builder() -> tuple[object, _CaptureExporter]:
    from agent_capture.span.builder import SpanBuilder

    exp = _CaptureExporter()
    return SpanBuilder(exp, default_compliance=_comp()), exp


def test_gate_blocks_side_effect_via_service(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text(_DENY_WIRE, encoding="utf-8")
    set_settings(Settings(rules_path=rules))
    set_gate(EnforcementClient(client=TestClient(create_app())))
    b, _exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="wire", attributes=_attrs(), builder=b)
    def wire() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with pytest.raises(EnforcementBlocked):
        wire()
    assert ran is False


def test_gate_allows_when_service_passes(tmp_path: Path) -> None:
    set_settings(Settings(rules_path=None))  # no rules -> allow
    set_gate(EnforcementClient(client=TestClient(create_app())))
    b, _exp = _builder()

    @traced(type=SpanType.SIDE_EFFECT, name="wire", attributes=_attrs(), builder=b)
    def wire() -> str:
        return "sent"

    assert wire() == "sent"


def test_gate_fails_open_when_service_down(caplog: pytest.LogCaptureFixture) -> None:
    set_gate(EnforcementClient(client=_raising_transport()))
    b, exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="wire", attributes=_attrs(), builder=b)
    def wire() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with caplog.at_level("ERROR", logger="agent_capture"):
        assert wire() == "sent"  # fallback allow -> host wins
    assert ran is True
    assert "AC502" in caplog.text
    assert exp.spans[0].status is SpanStatus.OK
