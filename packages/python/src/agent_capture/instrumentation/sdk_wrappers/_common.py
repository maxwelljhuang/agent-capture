"""Shared helpers for the SDK wrappers.

Each provider (Anthropic, OpenAI, Bedrock, Vertex) has slightly different
request/response shapes, but the lifecycle is the same: open a span,
run the original call, populate token counts / model info from the
response, close the span. This module owns the shared error-handling and
suppression discipline so each provider module stays small.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from agent_capture._internal.runtime import default_builder
from agent_capture._internal.safelog import ErrorCode, log_error, safelog
from agent_capture.context.propagation import model_call_suppressed, span_scope
from agent_capture.errors import CaptureError
from agent_capture.schema import ErrorInfo, SpanStatus, SpanType
from agent_capture.schema.types import ModelCallAttributes
from agent_capture.span.builder import OpenSpan, SpanBuilder

T = TypeVar("T")

RequestExtractor = Callable[[dict[str, Any]], ModelCallAttributes]
"""Build the initial attributes from the kwargs the user passed."""

ResponseAttacher = Callable[[ModelCallAttributes, Any], ModelCallAttributes]
"""Augment attributes after the response arrives (token counts, etc.)."""


def call_sync(
    *,
    func: Callable[..., T],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    provider: str,
    build_request_attrs: RequestExtractor,
    attach_response: ResponseAttacher,
    builder: SpanBuilder | None = None,
) -> T:
    """Run a sync model-provider call inside a model_call span.

    Honors :func:`model_call_suppressed`: when a framework adapter owns
    the span for this call (LangGraph etc.), passes through without
    opening a span of our own. Otherwise opens, calls, closes — and never
    raises into the caller for SDK-internal failures (the provider's own
    exception is re-raised so the host's control flow is preserved).
    """
    if model_call_suppressed():
        return func(*args, **kwargs)

    use_builder = builder or default_builder()
    if use_builder is None:
        safelog().debug("sdk_wrapper(%s): no builder configured; pass-through", provider)
        return func(*args, **kwargs)

    try:
        attrs = build_request_attrs(kwargs)
    except Exception as exc:
        log_error(
            ErrorCode.AC201,
            "sdk_wrapper(%s): build_request_attrs failed: %s",
            provider,
            exc,
        )
        return func(*args, **kwargs)

    try:
        open_span = use_builder.open(
            name=f"{provider}.messages.create",
            type=SpanType.MODEL_CALL,
            attributes=attrs,
            inputs=_safe_inputs(kwargs),
        )
    except Exception as exc:
        log_error(ErrorCode.AC202, "sdk_wrapper(%s): open failed: %s", provider, exc)
        return func(*args, **kwargs)

    with span_scope(open_span):
        try:
            response = func(*args, **kwargs)
        except BaseException as exc:
            _close_with_error(use_builder, open_span, exc)
            raise
        _finalize(use_builder, open_span, attrs, response, attach_response, provider)
        return response


async def call_async(
    *,
    func: Callable[..., Awaitable[T]],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    provider: str,
    build_request_attrs: RequestExtractor,
    attach_response: ResponseAttacher,
    builder: SpanBuilder | None = None,
) -> T:
    """Async variant of :func:`call_sync`."""
    if model_call_suppressed():
        return await func(*args, **kwargs)

    use_builder = builder or default_builder()
    if use_builder is None:
        safelog().debug("sdk_wrapper(%s): no builder configured; pass-through", provider)
        return await func(*args, **kwargs)

    try:
        attrs = build_request_attrs(kwargs)
    except Exception as exc:
        log_error(
            ErrorCode.AC201,
            "sdk_wrapper(%s): build_request_attrs failed: %s",
            provider,
            exc,
        )
        return await func(*args, **kwargs)

    try:
        open_span = use_builder.open(
            name=f"{provider}.messages.create",
            type=SpanType.MODEL_CALL,
            attributes=attrs,
            inputs=_safe_inputs(kwargs),
        )
    except Exception as exc:
        log_error(ErrorCode.AC202, "sdk_wrapper(%s): open failed: %s", provider, exc)
        return await func(*args, **kwargs)

    with span_scope(open_span):
        try:
            response = await func(*args, **kwargs)
        except BaseException as exc:
            _close_with_error(use_builder, open_span, exc)
            raise
        _finalize(use_builder, open_span, attrs, response, attach_response, provider)
        return response


def _finalize(
    builder: SpanBuilder,
    open_span: OpenSpan,
    attrs: ModelCallAttributes,
    response: Any,
    attach_response: ResponseAttacher,
    provider: str,
) -> None:
    try:
        enriched = attach_response(attrs, response)
        open_span.attributes = enriched
    except Exception as exc:
        log_error(
            ErrorCode.AC203,
            "sdk_wrapper(%s): attach_response failed: %s",
            provider,
            exc,
        )
    builder.close(open_span, outputs=_safe_outputs(response))


def _close_with_error(builder: SpanBuilder, open_span: OpenSpan, exc: BaseException) -> None:
    with contextlib.suppress(CaptureError):
        builder.close(
            open_span,
            status=SpanStatus.ERROR,
            error=ErrorInfo(
                error_type=f"{exc.__class__.__module__}.{exc.__class__.__qualname__}",
                message=str(exc),
            ),
        )


def _safe_inputs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Best-effort serialization of the request kwargs. Pre-redaction."""
    out: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in {"api_key", "auth_token"}:
            out[k] = "[REDACTED]"
            continue
        out[k] = _to_jsonable(v)
    return out


def _safe_outputs(response: Any) -> Any:
    """Coerce the response to a JSON-friendly form."""
    return _to_jsonable(response)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    # Pydantic v2 models, OpenAI / Anthropic response objects, etc.
    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return _to_jsonable(method())
            except Exception:
                pass
    try:
        return repr(value)
    except Exception:
        return "<unrepresentable>"


def is_coroutine_method(method: Callable[..., Any]) -> bool:
    """True if the bound method is a coroutine function (async)."""
    return inspect.iscoroutinefunction(method)
