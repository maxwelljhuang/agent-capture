"""Anthropic SDK wrapper integration tests.

Uses a hand-rolled fake client that mimics the relevant slice of the
anthropic SDK shape (``client.messages.create(model=..., messages=..., ...)``
returning a response with ``usage.input_tokens`` / ``output_tokens``).
This keeps the test isolated from the real SDK while exercising the
monkey-patching path the production wrapper takes.
"""

from __future__ import annotations

import pytest

from agent_capture.context.propagation import suppress_model_call_capture
from agent_capture.exporter.base import SpanExporter
from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap as wrap_anthropic
from agent_capture.schema import (
    ComplianceMetadata,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.span.builder import SpanBuilder


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, model: str, text: str, usage: _Usage) -> None:
        self.id = "msg_test_1"
        self.model = model
        self.content = [{"type": "text", "text": text}]
        self.usage = usage
        self.stop_reason = "end_turn"


class _Messages:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Response(
            model=kwargs.get("model", "unknown"),
            text="hi there",
            usage=_Usage(input_tokens=42, output_tokens=8),
        )


class _AsyncMessages:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return _Response(
            model=kwargs.get("model", "unknown"),
            text="hi there",
            usage=_Usage(input_tokens=11, output_tokens=3),
        )


class _FakeAnthropic:
    def __init__(self) -> None:
        self.messages = _Messages()


class _FakeAsyncAnthropic:
    def __init__(self) -> None:
        self.messages = _AsyncMessages()


class _CollectingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _builder() -> tuple[SpanBuilder, _CollectingExporter]:
    exp = _CollectingExporter()
    b = SpanBuilder(
        exp,
        default_compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
    )
    return b, exp


def test_sync_wrap_emits_model_call_span() -> None:
    builder, exp = _builder()
    client = wrap_anthropic(_FakeAnthropic(), builder=builder)

    response = client.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=512,
        temperature=0.2,
    )
    assert response.usage.input_tokens == 42

    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.type is SpanType.MODEL_CALL
    assert span.attributes.kind == "model_call"
    assert span.attributes.model_name == "claude-opus-4-7"
    assert span.attributes.provider == "anthropic"
    assert span.attributes.temperature == 0.2
    assert span.attributes.max_tokens == 512
    assert span.attributes.input_tokens == 42
    assert span.attributes.output_tokens == 8
    assert span.attributes.total_tokens == 50
    assert span.status is SpanStatus.OK


async def test_async_wrap_emits_model_call_span() -> None:
    builder, exp = _builder()
    client = wrap_anthropic(_FakeAsyncAnthropic(), builder=builder)
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        messages=[],
        max_tokens=16,
    )
    assert response.usage.input_tokens == 11

    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.attributes.input_tokens == 11
    assert span.attributes.output_tokens == 3


def test_underlying_exception_captured_and_reraised() -> None:
    builder, exp = _builder()

    class _Boom:
        def __init__(self) -> None:
            self.messages = self
            self.last_kwargs = None

        def create(self, **kwargs):
            self.last_kwargs = kwargs
            raise RuntimeError("api unreachable")

    client = wrap_anthropic(_Boom(), builder=builder)
    with pytest.raises(RuntimeError, match="api unreachable"):
        client.messages.create(model="x", messages=[])

    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.status is SpanStatus.ERROR
    assert span.error is not None
    assert "RuntimeError" in span.error.error_type


def test_suppress_flag_makes_wrapper_a_passthrough() -> None:
    """When the suppress flag is set (e.g. by a framework adapter), the
    wrapper must not emit a span — the framework owns the model_call."""
    builder, exp = _builder()
    client = wrap_anthropic(_FakeAnthropic(), builder=builder)
    with suppress_model_call_capture():
        response = client.messages.create(model="claude-opus-4-7", messages=[])
    assert response.usage.input_tokens == 42
    assert exp.spans == []


def test_no_builder_is_passthrough() -> None:
    """Without a configured builder the wrapper is a no-op."""
    fake = _FakeAnthropic()
    client = wrap_anthropic(fake)  # no builder= and no default registered
    response = client.messages.create(model="x", messages=[])
    assert response.usage.input_tokens == 42
