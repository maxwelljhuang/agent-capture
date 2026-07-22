"""HTTPExporter tests using respx for HTTP mocking."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import pytest
import respx

from agent_capture.exporter.http import HTTPExporter, _PermanentHTTPError
from agent_capture.exporter.retry import RetryPolicy, with_retry
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import (
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import PlannerStepAttributes


def _span(i: int = 0) -> Span:
    return Span(
        span_id=f"{i:016x}",
        parent_span_id=None,
        trajectory_id=f"{i:032x}",
        name=f"s{i}",
        type=SpanType.PLANNER_STEP,
        start_time=datetime(2026, 5, 17, tzinfo=UTC),
        end_time=datetime(2026, 5, 17, tzinfo=UTC),
        attributes=PlannerStepAttributes(),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(content_hash="0" * 64),
    )


def _fast_retry() -> RetryPolicy:
    return RetryPolicy(
        max_attempts=3,
        base_delay_s=0.001,
        max_delay_s=0.01,
        multiplier=2.0,
        jitter=0.0,
        sleep=lambda _: None,
    )


@respx.mock
def test_batches_flushed_on_size_threshold() -> None:
    route = respx.post("https://ledger.example/spans").mock(return_value=httpx.Response(200, json={"ok": True}))
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=3,
        batch_max_wait_s=0.05,
        retry_policy=_fast_retry(),
    )
    for i in range(3):
        exp.export(_span(i))
    # Give the worker a beat to flush.
    _wait_for(lambda: route.call_count >= 1, timeout=2.0)
    exp.shutdown(timeout=2.0)
    assert route.call_count >= 1
    body = json.loads(route.calls[0].request.content)
    assert len(body["spans"]) == 3


@respx.mock
def test_batches_flushed_on_time_threshold() -> None:
    route = respx.post("https://ledger.example/spans").mock(return_value=httpx.Response(200, json={"ok": True}))
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=100,  # very high so size never triggers
        batch_max_wait_s=0.05,
        retry_policy=_fast_retry(),
    )
    exp.export(_span(0))
    _wait_for(lambda: route.call_count >= 1, timeout=2.0)
    exp.shutdown(timeout=2.0)
    assert route.call_count == 1


@respx.mock
def test_5xx_retries_then_succeeds() -> None:
    route = respx.post("https://ledger.example/spans").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=1,
        batch_max_wait_s=0.05,
        retry_policy=_fast_retry(),
    )
    exp.export(_span(0))
    _wait_for(lambda: route.call_count >= 3, timeout=3.0)
    exp.shutdown(timeout=2.0)
    assert exp.dropped_count == 0
    assert route.call_count == 3


@respx.mock
def test_4xx_drops_without_retry() -> None:
    route = respx.post("https://ledger.example/spans").mock(return_value=httpx.Response(400, text="bad payload"))
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=1,
        batch_max_wait_s=0.05,
        retry_policy=_fast_retry(),
    )
    exp.export(_span(0))
    _wait_for(lambda: route.call_count >= 1, timeout=2.0)
    exp.shutdown(timeout=2.0)
    # 4xx is permanent — exactly one attempt, then drop.
    assert route.call_count == 1
    assert exp.dropped_count >= 1


@respx.mock
def test_failure_does_not_raise_into_caller() -> None:
    """The agent's hot path must never see a network exception."""
    respx.post("https://ledger.example/spans").mock(side_effect=httpx.ConnectError("network down"))
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=1,
        batch_max_wait_s=0.05,
        retry_policy=_fast_retry(),
    )
    # The producer never blocks or raises.
    exp.export(_span(0))
    exp.shutdown(timeout=2.0)
    assert exp.dropped_count >= 1


@respx.mock
def test_shutdown_flushes_remaining_buffer() -> None:
    route = respx.post("https://ledger.example/spans").mock(return_value=httpx.Response(200))
    exp = HTTPExporter(
        "https://ledger.example/spans",
        batch_size=100,
        batch_max_wait_s=60.0,  # would not flush on time before shutdown
        retry_policy=_fast_retry(),
    )
    exp.export(_span(0))
    exp.export(_span(1))
    exp.shutdown(timeout=2.0)
    assert route.call_count >= 1
    # Find the flushed batch and verify both spans are there.
    total = sum(len(json.loads(c.request.content)["spans"]) for c in route.calls)
    assert total == 2


def test_auth_header_set_when_provided(monkeypatch) -> None:
    with respx.mock(assert_all_called=False) as r:
        route = r.post("https://ledger.example/spans").mock(return_value=httpx.Response(200))
        exp = HTTPExporter(
            "https://ledger.example/spans",
            auth_token="tok-123",
            batch_size=1,
            batch_max_wait_s=0.05,
            retry_policy=_fast_retry(),
        )
        exp.export(_span(0))
        _wait_for(lambda: route.call_count >= 1, timeout=2.0)
        exp.shutdown(timeout=2.0)
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer tok-123"


# ---- retry helper unit -----------------------------------------------


def test_with_retry_eventually_succeeds() -> None:
    state = {"attempts": 0}

    def flaky() -> str:
        state["attempts"] += 1
        if state["attempts"] < 3:
            raise RuntimeError("nope")
        return "ok"

    assert with_retry(flaky, policy=_fast_retry()) == "ok"
    assert state["attempts"] == 3


def test_with_retry_gives_up_and_reraises() -> None:
    def always_fail() -> None:
        raise RuntimeError("persistent")

    with pytest.raises(RuntimeError, match="persistent"):
        with_retry(always_fail, policy=_fast_retry())


def test_with_retry_respects_retryable_predicate() -> None:
    state = {"attempts": 0}

    def fail() -> None:
        state["attempts"] += 1
        raise _PermanentHTTPError(400, "bad")

    with pytest.raises(_PermanentHTTPError):
        with_retry(
            fail,
            policy=_fast_retry(),
            retryable=lambda exc: not isinstance(exc, _PermanentHTTPError),
        )
    assert state["attempts"] == 1


# ---- helpers ----------------------------------------------------------


def _wait_for(predicate, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"predicate never became true within {timeout}s")
