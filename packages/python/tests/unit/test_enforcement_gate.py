"""Recorder-side enforcement gate hook (Phase 0): contract + no-op behavior.

These tests prove (a) zero behavior change when no gate is registered, (b) the
gate is consulted only for the two gated span types, (c) allow/block/hold
control flow, and (d) the cardinal-rule guarantee — an internal gate failure
never crashes the host (fail-open).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from agent_capture import traced
from agent_capture.enforcement import (
    GATED_TYPES,
    EnforcementBlocked,
    GateRequest,
    Verdict,
    current_gate,
    set_gate,
)
from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.queue import _CRITICAL_TYPES
from agent_capture.schema import Span, SpanStatus, SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.span.builder import SpanBuilder


class _CaptureExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _builder() -> tuple[SpanBuilder, _CaptureExporter]:
    exp = _CaptureExporter()
    return SpanBuilder(exp, default_compliance=_compliance()), exp


class _RecordingGate:
    """A test gate that records which span types it was consulted for."""

    def __init__(self, verdict: Verdict, *, raises: bool = False) -> None:
        self._verdict = verdict
        self._raises = raises
        self.seen: list[SpanType] = []

    def evaluate(self, request: GateRequest) -> Verdict:
        self.seen.append(request.span_type)
        if self._raises:
            raise RuntimeError("boom")
        return self._verdict

    async def evaluate_async(self, request: GateRequest) -> Verdict:
        self.seen.append(request.span_type)
        if self._raises:
            raise RuntimeError("boom")
        return self._verdict


@pytest.fixture(autouse=True)
def _reset_gate() -> Iterator[None]:
    """Every test starts and ends with no gate registered (global state)."""
    set_gate(None)
    yield
    set_gate(None)


# ---- drift guard ---------------------------------------------------------


def test_gated_types_match_critical_types() -> None:
    # GATED_TYPES must stay identical to the recorder's never-drop set.
    assert GATED_TYPES == _CRITICAL_TYPES


# ---- no gate registered (the default) ------------------------------------


def test_no_gate_side_effect_runs_normally() -> None:
    assert current_gate() is None
    b, exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="send_letter", builder=b)
    def send_letter() -> str:
        nonlocal ran
        ran = True
        return "sent"

    assert send_letter() == "sent"
    assert ran is True
    assert len(exp.spans) == 1
    assert exp.spans[0].status is SpanStatus.OK


# ---- gate consulted only for gated span types ----------------------------


def test_gate_consulted_only_for_gated_types() -> None:
    gate = _RecordingGate(Verdict(decision="allow"))
    set_gate(gate)
    b, _exp = _builder()

    @traced(type=SpanType.RETRIEVAL, name="fetch", builder=b)
    def fetch() -> int:
        return 1

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    def send() -> int:
        return 2

    @traced(type=SpanType.HUMAN_APPROVAL, name="review", builder=b)
    def review() -> int:
        return 3

    fetch()
    send()
    review()
    assert gate.seen == [SpanType.SIDE_EFFECT, SpanType.HUMAN_APPROVAL]


# ---- allow / block control flow (sync) -----------------------------------


def test_allow_runs_func() -> None:
    set_gate(_RecordingGate(Verdict(decision="allow")))
    b, exp = _builder()

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    def send() -> str:
        return "sent"

    assert send() == "sent"
    assert exp.spans[0].status is SpanStatus.OK


def test_block_stops_func_and_raises() -> None:
    set_gate(_RecordingGate(Verdict(decision="block", reason="missing approval", rule_id="r1")))
    b, exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    def send() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with pytest.raises(EnforcementBlocked, match="missing approval"):
        send()
    assert ran is False  # the side effect never executed
    assert len(exp.spans) == 1
    assert exp.spans[0].status is SpanStatus.ERROR
    assert exp.spans[0].error is not None
    # the blocked side_effect is recorded as not having succeeded
    assert exp.spans[0].attributes.success is False  # type: ignore[union-attr]


def test_hold_is_treated_as_block_failsafe() -> None:
    # A raw 'hold' reaching the decorator (gate didn't resolve it) is fail-safe.
    set_gate(_RecordingGate(Verdict(decision="hold", reason="pending review")))
    b, _exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    def send() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with pytest.raises(EnforcementBlocked):
        send()
    assert ran is False


# ---- allow / block control flow (async) ----------------------------------


def test_async_block_stops_func() -> None:
    set_gate(_RecordingGate(Verdict(decision="block", reason="nope")))
    b, _exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    async def send() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with pytest.raises(EnforcementBlocked):
        asyncio.run(send())
    assert ran is False


def test_async_allow_runs_func() -> None:
    set_gate(_RecordingGate(Verdict(decision="allow")))
    b, _exp = _builder()

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    async def send() -> str:
        return "sent"

    assert asyncio.run(send()) == "sent"


# ---- context-manager block -----------------------------------------------


def test_context_manager_block_skips_body() -> None:
    set_gate(_RecordingGate(Verdict(decision="block", reason="nope")))
    b, exp = _builder()
    ran = False

    with pytest.raises(EnforcementBlocked):
        with traced(type=SpanType.SIDE_EFFECT, name="send", builder=b):
            ran = True

    assert ran is False  # the with-body never executed
    assert len(exp.spans) == 1
    assert exp.spans[0].status is SpanStatus.ERROR


# ---- cardinal rule: gate bug never crashes the host (fail-open) -----------


def test_internal_gate_failure_fails_open(caplog: pytest.LogCaptureFixture) -> None:
    set_gate(_RecordingGate(Verdict(decision="block"), raises=True))
    b, exp = _builder()
    ran = False

    @traced(type=SpanType.SIDE_EFFECT, name="send", builder=b)
    def send() -> str:
        nonlocal ran
        ran = True
        return "sent"

    with caplog.at_level("ERROR", logger="agent_capture"):
        result = send()

    assert result == "sent"  # host won — fail-open on a gate bug
    assert ran is True
    assert "AC501" in caplog.text
    assert exp.spans[0].status is SpanStatus.OK
