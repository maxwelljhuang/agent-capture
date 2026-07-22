"""LiteLLM capture core — translates LiteLLM callback payloads into model_call spans.

Split out from ``litellm.py`` (the ``CustomLogger`` shim) so this logic is
importable and testable **without** litellm installed. The shim forwards
LiteLLM's callback hooks here. Field extraction is grounded in the real
``log_success_event`` payload: ``kwargs["model"]`` / ``["custom_llm_provider"]``
/ ``["optional_params"]`` and ``response_obj.usage.{prompt,completion,total}_tokens``.

A span opens at ``pre`` (real start time) and closes at ``success``/``failure``
(real end time), correlated by ``litellm_call_id`` — mirroring the LangGraph
adapter. Every entry point is wrapped so nothing raises into the host.
"""

from __future__ import annotations

import threading
from typing import Any

from agent_capture._internal.runtime import default_builder
from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.schema import ErrorInfo, SpanStatus, SpanType
from agent_capture.schema.types import ModelCallAttributes
from agent_capture.span.builder import OpenSpan, SpanBuilder


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _model_call_attrs(model: Any, kwargs: dict[str, Any], response_obj: Any = None) -> ModelCallAttributes:
    optional = kwargs.get("optional_params") or {}
    litellm_params = kwargs.get("litellm_params") or {}
    return ModelCallAttributes(
        model_name=str(kwargs.get("model") or model or getattr(response_obj, "model", None) or "unknown"),
        provider=str(kwargs.get("custom_llm_provider") or litellm_params.get("custom_llm_provider") or "unknown"),
        temperature=_as_float(optional.get("temperature", kwargs.get("temperature"))),
        max_tokens=_as_int(optional.get("max_tokens", kwargs.get("max_tokens"))),
    )


def _usage_patch(response_obj: Any) -> dict[str, int]:
    usage = getattr(response_obj, "usage", None)
    if usage is None:
        return {}
    patch: dict[str, int] = {}
    for src, dst in (
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        val = _as_int(getattr(usage, src, None))
        if val is not None:
            patch[dst] = val
    return patch


def _response_summary(response_obj: Any) -> dict[str, Any] | None:
    try:
        choices = getattr(response_obj, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message is not None else None
            if content is not None:
                return {"content": content}
    except Exception:  # never let output extraction break capture
        return None
    return None


class LiteLLMCapture:
    """Emits a ``model_call`` span per LiteLLM completion. Never raises into the host."""

    def __init__(self, builder: SpanBuilder | None = None) -> None:
        self._builder = builder
        self._open: dict[str, OpenSpan] = {}
        self._lock = threading.Lock()

    def _builder_or_default(self) -> SpanBuilder | None:
        return self._builder or default_builder()

    def pre(self, model: Any, kwargs: dict[str, Any]) -> None:
        try:
            builder = self._builder_or_default()
            if builder is None:
                return
            span = builder.open(
                name="llm",
                type=SpanType.MODEL_CALL,
                attributes=_model_call_attrs(model, kwargs),
                inputs={"messages": kwargs.get("messages")},
            )
            call_id = kwargs.get("litellm_call_id")
            if call_id is not None:
                with self._lock:
                    self._open[call_id] = span
        except Exception as exc:
            log_error(ErrorCode.AC204, "litellm pre_api_call capture failed: %s", exc)

    def _take(self, builder: SpanBuilder, kwargs: dict[str, Any], response_obj: Any) -> OpenSpan:
        """Pop the span opened at pre, or open a fresh one (pre may not have fired)."""
        call_id = kwargs.get("litellm_call_id")
        span: OpenSpan | None = None
        if call_id is not None:
            with self._lock:
                span = self._open.pop(call_id, None)
        if span is None:
            span = builder.open(
                name="llm",
                type=SpanType.MODEL_CALL,
                attributes=_model_call_attrs(kwargs.get("model"), kwargs, response_obj),
            )
        return span

    def success(self, kwargs: dict[str, Any], response_obj: Any, start_time: Any = None, end_time: Any = None) -> None:
        try:
            builder = self._builder_or_default()
            if builder is None:
                return
            span = self._take(builder, kwargs, response_obj)
            if isinstance(span.attributes, ModelCallAttributes):
                patch = _usage_patch(response_obj)
                if patch:
                    span.attributes = span.attributes.model_copy(update=patch)
            builder.close(span, outputs=_response_summary(response_obj))
        except Exception as exc:
            log_error(ErrorCode.AC205, "litellm success capture failed: %s", exc)

    def failure(self, kwargs: dict[str, Any], response_obj: Any, start_time: Any = None, end_time: Any = None) -> None:
        try:
            builder = self._builder_or_default()
            if builder is None:
                return
            span = self._take(builder, kwargs, response_obj)
            exc = kwargs.get("exception")
            error = ErrorInfo(
                error_type=type(exc).__name__ if exc else "LLMError", message=str(exc or "litellm call failed")
            )
            builder.close(span, status=SpanStatus.ERROR, error=error)
        except Exception as exc:
            log_error(ErrorCode.AC206, "litellm failure capture failed: %s", exc)
