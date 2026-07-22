"""Anthropic SDK wrapper.

Usage::

    from anthropic import Anthropic
    from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap

    client = wrap(Anthropic())
    msg = client.messages.create(
        model="claude-opus-4-7",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=512,
    )

After ``wrap``, every ``client.messages.create(...)`` call emits a
``model_call`` span via the configured span builder. Works on both
``Anthropic`` (sync) and ``AsyncAnthropic`` clients — the wrapper
auto-detects.

Honors :func:`agent_capture.context.model_call_suppressed`: when a
framework adapter has already opened a ``model_call`` span for the
current scope, the wrapper passes through without emitting a duplicate.
"""

from __future__ import annotations

import functools
from typing import Any, TypeVar

from agent_capture.instrumentation.sdk_wrappers._common import (
    call_async,
    call_sync,
    is_coroutine_method,
)
from agent_capture.schema.types import ModelCallAttributes
from agent_capture.span.builder import SpanBuilder

C = TypeVar("C")

_PROVIDER = "anthropic"


def _build_request_attrs(kwargs: dict[str, Any]) -> ModelCallAttributes:
    return ModelCallAttributes(
        model_name=str(kwargs.get("model", "unknown")),
        provider=_PROVIDER,
        temperature=kwargs.get("temperature"),
        max_tokens=kwargs.get("max_tokens"),
    )


def _attach_response(attrs: ModelCallAttributes, response: Any) -> ModelCallAttributes:
    usage = _extract_usage(response)
    response_model = _extract_attr(response, "model")
    has_distinct_version = response_model and response_model != attrs.model_name
    return attrs.model_copy(
        update={
            "model_version": response_model if has_distinct_version else attrs.model_version,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": (
                (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                if usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
                else None
            ),
        }
    )


def _extract_usage(response: Any) -> dict[str, int | None]:
    """Best-effort extraction of token counts from an Anthropic response.

    Anthropic returns ``response.usage.input_tokens`` and
    ``response.usage.output_tokens`` on ``Message`` objects.
    """
    usage = _extract_attr(response, "usage")
    if usage is None:
        return {"input_tokens": None, "output_tokens": None}
    return {
        "input_tokens": _extract_attr(usage, "input_tokens"),
        "output_tokens": _extract_attr(usage, "output_tokens"),
    }


def _extract_attr(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def wrap(client: C, *, builder: SpanBuilder | None = None) -> C:
    """Patch an Anthropic client so its ``messages.create`` emits spans.

    Args:
        client: An ``anthropic.Anthropic`` or ``anthropic.AsyncAnthropic``
            instance (or any duck-type with ``client.messages.create``).
        builder: Optional explicit builder. Defaults to the process-wide
            builder registered by :func:`agent_capture.configure`.

    Returns the same client (mutated in place) for fluent usage:
    ``client = wrap(Anthropic())``.
    """
    messages = getattr(client, "messages", None)
    if messages is None:
        raise AttributeError(
            "wrap_anthropic: client has no .messages attribute. Expected an Anthropic() or AsyncAnthropic() instance."
        )
    create = getattr(messages, "create", None)
    if create is None or not callable(create):
        raise AttributeError("wrap_anthropic: client.messages.create not callable.")

    if is_coroutine_method(create):

        @functools.wraps(create)
        async def wrapped_async(*args: Any, **kwargs: Any) -> Any:
            return await call_async(
                func=create,
                args=args,
                kwargs=kwargs,
                provider=_PROVIDER,
                build_request_attrs=_build_request_attrs,
                attach_response=_attach_response,
                builder=builder,
            )

        messages.create = wrapped_async
    else:

        @functools.wraps(create)
        def wrapped_sync(*args: Any, **kwargs: Any) -> Any:
            return call_sync(
                func=create,
                args=args,
                kwargs=kwargs,
                provider=_PROVIDER,
                build_request_attrs=_build_request_attrs,
                attach_response=_attach_response,
                builder=builder,
            )

        messages.create = wrapped_sync

    return client
