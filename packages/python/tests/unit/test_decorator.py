"""@traced decorator + context manager tests."""

from __future__ import annotations

import asyncio

import pytest

from agent_capture import traced
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span, SpanStatus, SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import RetrievalAttributes
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


def test_decorator_emits_single_span() -> None:
    b, exp = _builder()

    @traced(type=SpanType.RETRIEVAL, name="fetch", builder=b)
    def fetch(applicant_id: str) -> dict[str, object]:
        return {"score": 700}

    result = fetch("abc-123")
    assert result == {"score": 700}
    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.name == "fetch"
    assert span.type is SpanType.RETRIEVAL
    assert span.inputs == {"applicant_id": "abc-123"}
    assert span.outputs == {"score": 700}
    assert span.status is SpanStatus.OK


def test_decorator_captures_exception_and_reraises() -> None:
    b, exp = _builder()

    @traced(type=SpanType.RETRIEVAL, builder=b)
    def bad() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        bad()

    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.status is SpanStatus.ERROR
    assert span.error is not None
    assert "ValueError" in span.error.error_type
    assert span.error.message == "nope"


async def test_async_decorator_works() -> None:
    b, exp = _builder()

    @traced(type=SpanType.MODEL_CALL, builder=b)
    async def call() -> str:
        await asyncio.sleep(0)
        return "answer"

    result = await call()
    assert result == "answer"
    assert len(exp.spans) == 1
    assert exp.spans[0].type is SpanType.MODEL_CALL


def test_context_manager_emits_span() -> None:
    b, exp = _builder()
    with traced(type=SpanType.PLANNER_STEP, name="decide", builder=b):
        pass
    assert len(exp.spans) == 1
    assert exp.spans[0].name == "decide"
    assert exp.spans[0].type is SpanType.PLANNER_STEP


def test_context_manager_propagates_exception() -> None:
    b, exp = _builder()
    with pytest.raises(RuntimeError, match="boom"):
        with traced(type=SpanType.PLANNER_STEP, builder=b):
            raise RuntimeError("boom")
    assert len(exp.spans) == 1
    assert exp.spans[0].status is SpanStatus.ERROR


def test_nested_decorators_chain_through_parent() -> None:
    b, exp = _builder()

    @traced(
        type=SpanType.RETRIEVAL,
        name="inner",
        attributes=RetrievalAttributes(source_identifier="experian"),
        builder=b,
    )
    def inner() -> str:
        return "data"

    @traced(type=SpanType.PLANNER_STEP, name="outer", builder=b)
    def outer() -> str:
        return inner()

    outer()
    # Two spans, outer is root (shipped last), inner is child.
    assert [s.name for s in exp.spans] == ["inner", "outer"]
    child, root = exp.spans
    assert child.parent_span_id == root.span_id
    assert child.provenance.parent_content_hash == root.provenance.content_hash


def test_decorator_is_passthrough_without_builder() -> None:
    """No global builder + no override = decorator becomes a no-op wrapper."""

    @traced(type=SpanType.RETRIEVAL)
    def f(x: int) -> int:
        return x * 2

    assert f(21) == 42
