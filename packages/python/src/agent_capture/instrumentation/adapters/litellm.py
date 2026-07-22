"""LiteLLM framework adapter.

LiteLLM is the single LLM choke point in many agents (a unified client over
100+ providers). **Instrument here, not at your agent class** — orchestrators
often call the LLM client directly (intent detection, routing, RAG), bypassing
the agent class, which would produce zero ``model_call`` spans.

Usage::

    from agent_capture.instrumentation.adapters.litellm import install
    install()                       # registers the callback on litellm.callbacks
    # ... now every litellm.completion()/acompletion()/Router call under an
    # active @traced scope emits a model_call span (model, provider, tokens).

Requires the ``litellm`` extra: ``pip install 'agent-capture[litellm]'``. The
span-building logic lives in :mod:`._litellm_core` (importable without litellm);
this module is the thin :class:`litellm.integrations.custom_logger.CustomLogger`
shim that forwards LiteLLM's callbacks to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_capture.instrumentation.adapters._litellm_core import LiteLLMCapture
from agent_capture.span.builder import SpanBuilder

if TYPE_CHECKING:
    from litellm.integrations.custom_logger import CustomLogger as _CustomLogger
else:
    try:
        from litellm.integrations.custom_logger import CustomLogger as _CustomLogger
    except ImportError as exc:  # pragma: no cover — exercised by users who skip the extra
        raise ImportError(
            "agent_capture.instrumentation.adapters.litellm requires the `litellm` "
            "extra. Install with: pip install 'agent-capture[litellm]'"
        ) from exc


class LiteLLMCaptureCallback(_CustomLogger):
    """LiteLLM ``CustomLogger`` that emits an agent-capture ``model_call`` span per call."""

    def __init__(self, builder: SpanBuilder | None = None) -> None:
        super().__init__()
        self._cap = LiteLLMCapture(builder)

    # sync hooks
    def log_pre_api_call(self, model: Any, messages: Any, kwargs: dict[str, Any]) -> None:
        self._cap.pre(model, kwargs)

    def log_success_event(self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any) -> None:
        self._cap.success(kwargs, response_obj, start_time, end_time)

    def log_failure_event(self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any) -> None:
        self._cap.failure(kwargs, response_obj, start_time, end_time)

    # async hooks
    async def async_log_pre_api_call(self, model: Any, messages: Any, kwargs: dict[str, Any]) -> None:
        self._cap.pre(model, kwargs)

    async def async_log_success_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self._cap.success(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self, kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self._cap.failure(kwargs, response_obj, start_time, end_time)


def install(builder: SpanBuilder | None = None) -> LiteLLMCaptureCallback:
    """Register a capture callback on ``litellm.callbacks`` and return it."""
    import litellm

    callback = LiteLLMCaptureCallback(builder)
    litellm.callbacks.append(callback)
    return callback


__all__ = ["LiteLLMCaptureCallback", "install"]
