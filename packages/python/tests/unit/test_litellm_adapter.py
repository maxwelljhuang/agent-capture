"""LiteLLM capture core — model_call emission from realistic callback payloads.

Drives the litellm-free core (`_litellm_core.LiteLLMCapture`) with the payload
shape verified against real litellm (kwargs + a ModelResponse-like object) so no
litellm install is needed here.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_capture.instrumentation.adapters._litellm_core import LiteLLMCapture
from agent_capture.schema import (
    ComplianceMetadata,
    RegulatoryRegime,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.span.builder import SpanBuilder


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="a@0.1",
        end_customer_id="acme",
        regulatory_regime=[RegulatoryRegime.ECOA],
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


class _Cap:
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _resp(model: str = "gpt-4", pt: int = 10, ct: int = 20, tt: int = 30, content: str = "hi") -> SimpleNamespace:
    usage = SimpleNamespace(prompt_tokens=pt, completion_tokens=ct, total_tokens=tt)
    return SimpleNamespace(
        model=model, usage=usage, choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _kwargs(call_id: str | None = "call-1") -> dict:
    return {
        "model": "gpt-4",
        "litellm_call_id": call_id,
        "custom_llm_provider": "openai",
        "optional_params": {"temperature": 0.2, "max_tokens": 64},
        "messages": [{"role": "user", "content": "hi"}],
    }


def _builder() -> tuple[SpanBuilder, _Cap]:
    cap = _Cap()
    return SpanBuilder(cap, default_compliance=_compliance()), cap  # type: ignore[arg-type]


def test_pre_then_success_emits_model_call() -> None:
    builder, cap = _builder()
    capture = LiteLLMCapture(builder)
    kw = _kwargs()
    capture.pre("gpt-4", kw)
    assert cap.spans == []  # nothing shipped until success
    capture.success(kw, _resp())
    assert len(cap.spans) == 1
    span = cap.spans[0]
    assert span.type == SpanType.MODEL_CALL
    assert span.attributes.model_name == "gpt-4"
    assert span.attributes.provider == "openai"
    assert span.attributes.temperature == 0.2
    assert span.attributes.max_tokens == 64
    assert span.attributes.input_tokens == 10
    assert span.attributes.output_tokens == 20
    assert span.attributes.total_tokens == 30


def test_success_without_pre_falls_back_to_single_span() -> None:
    builder, cap = _builder()
    capture = LiteLLMCapture(builder)
    capture.success(_kwargs(call_id=None), _resp())  # pre never fired
    assert len(cap.spans) == 1
    assert cap.spans[0].attributes.total_tokens == 30


def test_failure_emits_error_span() -> None:
    builder, cap = _builder()
    capture = LiteLLMCapture(builder)
    kw = _kwargs()
    kw["exception"] = ValueError("provider down")
    capture.pre("gpt-4", kw)
    capture.failure(kw, None)
    assert len(cap.spans) == 1
    assert cap.spans[0].status.value == "error"
    assert cap.spans[0].error is not None


def test_no_builder_no_span_no_raise() -> None:
    # No builder passed and no global default configured → no-op, never raises.
    capture = LiteLLMCapture(builder=None)
    capture.pre("gpt-4", _kwargs())
    capture.success(_kwargs(), _resp())  # must not raise


def test_garbage_payload_never_raises() -> None:
    builder, _ = _builder()
    capture = LiteLLMCapture(builder)
    capture.pre("gpt-4", {"litellm_call_id": "x"})  # minimal kwargs
    capture.success({"litellm_call_id": "x"}, object())  # response with no usage/choices
