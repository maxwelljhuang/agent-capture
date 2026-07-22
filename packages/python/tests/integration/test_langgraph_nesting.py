"""LangGraph adapter integration tests.

Drives the :class:`CaptureCallbackHandler` directly with synthetic
callback events that mirror what LangChain/LangGraph emits during a real
graph run. Avoids constructing a full LangGraph runnable so the test
stays fast and dependency-light, but the callback shapes match what
``langchain_core`` documents.

The critical assertion: when the framework adapter fires ``on_llm_start``
*and* a wrapped Anthropic client emits its own ``messages.create``, only
**one** model_call span should be in the resulting trajectory — the one
owned by the framework adapter. The SDK wrapper's suppression flag is the
mechanism.
"""

# ruff: noqa: E402 — pytest.importorskip MUST run before the agent_capture imports
#                    that would otherwise fail if langchain-core isn't installed.

from __future__ import annotations

import uuid

import pytest

# Skip the whole file if the langgraph extra isn't installed.
langchain_core = pytest.importorskip("langchain_core")

from agent_capture.exporter.base import SpanExporter
from agent_capture.instrumentation.adapters.langgraph import CaptureCallbackHandler
from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap as wrap_anthropic
from agent_capture.schema import (
    ComplianceMetadata,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.span.builder import SpanBuilder


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


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _AnthropicResponse:
    def __init__(self) -> None:
        self.id = "msg_1"
        self.model = "claude-opus-4-7"
        self.content = [{"type": "text", "text": "answer"}]
        self.usage = _Usage(input_tokens=50, output_tokens=10)


class _FakeAnthropic:
    class _Messages:
        def create(self, **kwargs):
            return _AnthropicResponse()

    def __init__(self) -> None:
        self.messages = _FakeAnthropic._Messages()


class _FakeLLMResult:
    """Minimal stand-in for langchain_core.outputs.LLMResult."""

    def __init__(self) -> None:
        self.generations = [[{"text": "answer"}]]
        self.llm_output = {"token_usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}


def test_chain_with_nested_tool_and_llm_produces_three_spans() -> None:
    builder, exp = _builder()
    handler = CaptureCallbackHandler(builder=builder)

    chain_run = uuid.uuid4()
    tool_run = uuid.uuid4()
    llm_run = uuid.uuid4()

    handler.on_chain_start({"name": "underwrite"}, {"app_id": "9001"}, run_id=chain_run)
    handler.on_tool_start(
        {"name": "fetch_credit_report"},
        "applicant=abc",
        run_id=tool_run,
        parent_run_id=chain_run,
    )
    handler.on_tool_end({"score": 700}, run_id=tool_run)
    handler.on_llm_start(
        {"name": "claude"},
        ["Decide: approve or deny?"],
        run_id=llm_run,
        parent_run_id=chain_run,
        invocation_params={"model": "claude-opus-4-7", "temperature": 0.0, "max_tokens": 128, "provider": "anthropic"},
    )
    handler.on_llm_end(_FakeLLMResult(), run_id=llm_run)
    handler.on_chain_end({"decision": "deny"}, run_id=chain_run)

    # Ship order is leaves-first: tool, llm, chain.
    assert [s.name for s in exp.spans] == ["fetch_credit_report", "claude", "underwrite"]
    tool_span, llm_span, chain_span = exp.spans
    assert tool_span.type is SpanType.TOOL_CALL
    assert llm_span.type is SpanType.MODEL_CALL
    assert chain_span.type is SpanType.PLANNER_STEP

    # Parent-child wiring matches the run_id hierarchy.
    assert chain_span.parent_span_id is None
    assert tool_span.parent_span_id == chain_span.span_id
    assert llm_span.parent_span_id == chain_span.span_id

    # Hash chain links correctly.
    assert tool_span.provenance.parent_content_hash == chain_span.provenance.content_hash
    assert llm_span.provenance.parent_content_hash == chain_span.provenance.content_hash

    # LLM span absorbed the token counts from llm_output.
    assert llm_span.attributes.input_tokens == 50
    assert llm_span.attributes.output_tokens == 10
    assert llm_span.attributes.total_tokens == 60
    assert llm_span.attributes.model_name == "claude-opus-4-7"


def test_no_double_counting_when_sdk_wrapper_runs_under_llm_callback() -> None:
    """The architecture's dedup requirement.

    When LangGraph fires on_llm_start, it owns the model_call span. If the
    underlying call goes through a wrapped Anthropic client, the wrapper
    must NOT emit a second model_call span — exactly one survives in the
    trajectory.
    """
    builder, exp = _builder()
    handler = CaptureCallbackHandler(builder=builder)
    wrapped_client = wrap_anthropic(_FakeAnthropic(), builder=builder)

    chain_run = uuid.uuid4()
    llm_run = uuid.uuid4()

    handler.on_chain_start({"name": "decide"}, {}, run_id=chain_run)
    handler.on_llm_start(
        {"name": "claude"},
        ["q"],
        run_id=llm_run,
        parent_run_id=chain_run,
        invocation_params={"model": "claude-opus-4-7"},
    )
    # Here is the critical moment: LangChain would normally invoke the LLM
    # SDK between on_llm_start and on_llm_end. The SDK wrapper sees the
    # suppress flag set by the handler and passes through.
    response = wrapped_client.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "q"}],
    )
    assert response.usage.input_tokens == 50

    handler.on_llm_end(_FakeLLMResult(), run_id=llm_run)
    handler.on_chain_end({"decision": "deny"}, run_id=chain_run)

    model_calls = [s for s in exp.spans if s.type is SpanType.MODEL_CALL]
    assert len(model_calls) == 1, f"expected exactly one model_call span, got {len(model_calls)}"
    # And the one we got is the framework-owned one (it has the prompts in inputs).
    assert model_calls[0].inputs is not None
    assert "prompts" in model_calls[0].inputs


def test_chain_error_records_error_span() -> None:
    builder, exp = _builder()
    handler = CaptureCallbackHandler(builder=builder)
    run = uuid.uuid4()

    handler.on_chain_start({"name": "boom"}, {}, run_id=run)
    handler.on_chain_error(RuntimeError("kaboom"), run_id=run)

    assert len(exp.spans) == 1
    span = exp.spans[0]
    assert span.status is SpanStatus.ERROR
    assert span.error is not None
    assert "RuntimeError" in span.error.error_type
    assert span.error.message == "kaboom"


def test_unknown_run_id_callbacks_are_safe() -> None:
    """A close without a matching open must not raise."""
    builder, _ = _builder()
    handler = CaptureCallbackHandler(builder=builder)
    handler.on_chain_end({}, run_id=uuid.uuid4())  # never started — no-op
    handler.on_tool_error(RuntimeError("x"), run_id=uuid.uuid4())  # no-op
