"""OpenAI SDK wrapper.

Usage::

    from openai import OpenAI
    from agent_capture.instrumentation.sdk_wrappers.openai import wrap

    client = wrap(OpenAI())
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hello"}],
    )

Patches ``client.chat.completions.create`` (both sync ``OpenAI`` and async
``AsyncOpenAI``). Honors :func:`agent_capture.context.model_call_suppressed`
so framework adapters that own the model_call span don't double-count.
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

_PROVIDER = "openai"


def _build_request_attrs(kwargs: dict[str, Any]) -> ModelCallAttributes:
    return ModelCallAttributes(
        model_name=str(kwargs.get("model", "unknown")),
        provider=_PROVIDER,
        temperature=kwargs.get("temperature"),
        max_tokens=kwargs.get("max_tokens") or kwargs.get("max_completion_tokens"),
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
            "total_tokens": usage.get("total_tokens"),
        }
    )


def _extract_usage(response: Any) -> dict[str, int | None]:
    """OpenAI returns ``response.usage.prompt_tokens / completion_tokens / total_tokens``."""
    usage = _extract_attr(response, "usage")
    if usage is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    return {
        "input_tokens": _extract_attr(usage, "prompt_tokens"),
        "output_tokens": _extract_attr(usage, "completion_tokens"),
        "total_tokens": _extract_attr(usage, "total_tokens"),
    }


def _extract_attr(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def wrap(client: C, *, builder: SpanBuilder | None = None) -> C:
    """Patch an OpenAI client so ``chat.completions.create`` emits spans.

    Args:
        client: An ``openai.OpenAI`` or ``openai.AsyncOpenAI`` instance.
        builder: Optional explicit builder; defaults to the configured one.
    """
    chat = getattr(client, "chat", None)
    if chat is None:
        raise AttributeError(
            "wrap_openai: client has no .chat attribute. Expected an OpenAI() or AsyncOpenAI() instance."
        )
    completions = getattr(chat, "completions", None)
    if completions is None:
        raise AttributeError("wrap_openai: client.chat.completions missing.")
    create = getattr(completions, "create", None)
    if create is None or not callable(create):
        raise AttributeError("wrap_openai: client.chat.completions.create not callable.")

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

        completions.create = wrapped_async
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

        completions.create = wrapped_sync

    return client
