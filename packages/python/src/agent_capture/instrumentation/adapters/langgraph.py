"""LangGraph (and LangChain) framework adapter.

Register :class:`CaptureCallbackHandler` with a LangGraph or LangChain
runnable and it emits a coherent trajectory automatically. The framework
already maintains parent-child relationships internally via ``run_id`` /
``parent_run_id``; this handler translates each lifecycle hook into a
``SpanBuilder.open(parent=...)`` or ``close()`` call.

Coverage:

- ``on_chain_start`` / ``on_chain_end`` / ``on_chain_error``
  → :class:`SpanType.PLANNER_STEP`
- ``on_llm_start`` / ``on_llm_end`` / ``on_llm_error``
  → :class:`SpanType.MODEL_CALL` *and* suppresses the Anthropic/OpenAI
  SDK wrappers for the duration of the call so we don't double-count.
- ``on_tool_start`` / ``on_tool_end`` / ``on_tool_error``
  → :class:`SpanType.TOOL_CALL`
- ``on_retriever_start`` / ``on_retriever_end`` / ``on_retriever_error``
  → :class:`SpanType.RETRIEVAL`

This module imports from ``langchain_core`` lazily — install the
``langgraph`` extra (which pulls ``langchain-core``) to use it. The
import is deferred so importing :mod:`agent_capture.instrumentation`
doesn't require LangChain.
"""

from __future__ import annotations

import contextlib
from contextvars import Token
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agent_capture._internal.runtime import default_builder
from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.context.propagation import _suppress_model_call
from agent_capture.schema import ErrorInfo, SpanStatus, SpanType
from agent_capture.schema.types import (
    ModelCallAttributes,
    PlannerStepAttributes,
    RetrievalAttributes,
    ToolCallAttributes,
)
from agent_capture.span.builder import OpenSpan, SpanBuilder

if TYPE_CHECKING:
    from langchain_core.callbacks import BaseCallbackHandler as _BaseCallbackHandler
else:
    try:
        from langchain_core.callbacks import BaseCallbackHandler as _BaseCallbackHandler
    except ImportError as exc:  # pragma: no cover — exercised by users who skip the extra
        raise ImportError(
            "agent_capture.instrumentation.adapters.langgraph requires the "
            "`langgraph` extra. Install with: pip install 'agent-capture[langgraph]'"
        ) from exc


class CaptureCallbackHandler(_BaseCallbackHandler):
    """LangChain/LangGraph ``BaseCallbackHandler`` that emits agent-capture spans.

    Instantiate once and pass to your runnable::

        from langchain_core.callbacks import CallbackManager
        from agent_capture.instrumentation.adapters.langgraph import CaptureCallbackHandler

        handler = CaptureCallbackHandler()
        result = graph.invoke(state, config={"callbacks": [handler]})

    Args:
        builder: Override builder; defaults to the process-wide one set by
            :func:`agent_capture.configure`.
    """

    raise_error: bool = False

    def __init__(self, *, builder: SpanBuilder | None = None) -> None:
        super().__init__()
        self._builder_override = builder
        self._open: dict[UUID, OpenSpan] = {}
        self._suppress_tokens: dict[UUID, Token[bool]] = {}

    # ---- internal helpers ------------------------------------------------

    def _builder(self) -> SpanBuilder | None:
        return self._builder_override or default_builder()

    def _start(
        self,
        *,
        name: str,
        type: SpanType,
        attributes: Any,
        run_id: UUID,
        parent_run_id: UUID | None,
        inputs: Any | None,
    ) -> None:
        b = self._builder()
        if b is None:
            return
        parent = self._open.get(parent_run_id) if parent_run_id is not None else None
        try:
            open_span = b.open(
                name=name,
                type=type,
                attributes=attributes,
                inputs=inputs,
                parent=parent,
            )
        except Exception as exc:
            log_error(ErrorCode.AC204, "langgraph adapter open failed: %s", exc)
            return
        self._open[run_id] = open_span

    def _end(self, run_id: UUID, *, outputs: Any | None = None) -> None:
        open_span = self._open.pop(run_id, None)
        if open_span is None:
            return
        b = self._builder()
        if b is None:
            return
        try:
            b.close(open_span, outputs=outputs)
        except Exception as exc:
            log_error(ErrorCode.AC205, "langgraph adapter close failed: %s", exc)

    def _error(self, run_id: UUID, error: BaseException) -> None:
        open_span = self._open.pop(run_id, None)
        if open_span is None:
            return
        b = self._builder()
        if b is None:
            return
        try:
            b.close(
                open_span,
                status=SpanStatus.ERROR,
                error=ErrorInfo(
                    error_type=f"{error.__class__.__module__}.{error.__class__.__qualname__}",
                    message=str(error),
                ),
            )
        except Exception as exc:
            log_error(
                ErrorCode.AC206,
                "langgraph adapter close-with-error failed: %s",
                exc,
            )

    # ---- chain (planner_step) lifecycle ---------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any] | Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            name=_label(serialized) or "chain",
            type=SpanType.PLANNER_STEP,
            attributes=PlannerStepAttributes(),
            run_id=run_id,
            parent_run_id=parent_run_id,
            inputs=inputs,
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any] | Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._end(run_id, outputs=outputs)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._error(run_id, error)

    # ---- LLM (model_call) lifecycle ------------------------------------

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        params = invocation_params or {}
        attrs = ModelCallAttributes(
            model_name=str(params.get("model") or params.get("model_name") or "unknown"),
            provider=str(params.get("_type") or params.get("provider") or "langchain"),
            temperature=params.get("temperature"),
            max_tokens=params.get("max_tokens"),
        )
        self._start(
            name=_label(serialized) or "llm",
            type=SpanType.MODEL_CALL,
            attributes=attrs,
            run_id=run_id,
            parent_run_id=parent_run_id,
            inputs={"prompts": prompts, "invocation_params": params},
        )
        # Suppress the SDK wrappers for this call so we don't double-count.
        self._suppress_tokens[run_id] = _suppress_model_call.set(True)

    # LangChain has separate on_chat_model_start with a different signature
    # (messages: list[list[BaseMessage]] instead of prompts: list[str]).
    # We forward to on_llm_start with a synthesized `prompts` field so the
    # span builder sees a consistent input shape.
    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        prompts = [str(m) for m in (messages or [])]
        self.on_llm_start(
            serialized,
            prompts,
            run_id=run_id,
            parent_run_id=parent_run_id,
            invocation_params=invocation_params,
            **kwargs,
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        token = self._suppress_tokens.pop(run_id, None)
        if token is not None:
            try:
                _suppress_model_call.reset(token)
            except ValueError:
                # ContextVar token can be reset only in the same context.
                # If callback boundaries crossed tasks we just leave the flag set —
                # the LLM call has already returned so it doesn't matter.
                pass
        # Patch token counts into the attributes before close.
        open_span = self._open.get(run_id)
        if open_span is not None and isinstance(open_span.attributes, ModelCallAttributes):
            usage = _extract_llm_usage(response)
            if usage:
                open_span.attributes = open_span.attributes.model_copy(update=usage)
        self._end(run_id, outputs=_serialize_llm_response(response))

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        token = self._suppress_tokens.pop(run_id, None)
        if token is not None:
            with contextlib.suppress(ValueError):
                _suppress_model_call.reset(token)
        self._error(run_id, error)

    # ---- tool (tool_call) lifecycle -----------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        name = _label(serialized) or "tool"
        self._start(
            name=name,
            type=SpanType.TOOL_CALL,
            attributes=ToolCallAttributes(tool_name=name),
            run_id=run_id,
            parent_run_id=parent_run_id,
            inputs={"input": input_str},
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._end(run_id, outputs={"output": _to_jsonable(output)})

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._error(run_id, error)

    # ---- retriever lifecycle -----------------------------------------

    def on_retriever_start(
        self,
        serialized: dict[str, Any] | None,
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            name=_label(serialized) or "retriever",
            type=SpanType.RETRIEVAL,
            attributes=RetrievalAttributes(
                source_identifier=_label(serialized) or "unknown",
                query=query,
            ),
            run_id=run_id,
            parent_run_id=parent_run_id,
            inputs={"query": query},
        )

    def on_retriever_end(
        self,
        documents: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._end(run_id, outputs={"documents": _to_jsonable(documents)})

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._error(run_id, error)


# ---- module-private helpers ----------------------------------------------


def _label(serialized: dict[str, Any] | None) -> str | None:
    if not serialized:
        return None
    if serialized.get("name"):
        return str(serialized["name"])
    if "id" in serialized and isinstance(serialized["id"], list):
        return str(serialized["id"][-1])
    return None


def _extract_llm_usage(response: Any) -> dict[str, Any]:
    """Pull token counts from a LangChain ``LLMResult`` if present.

    LangChain stores usage under ``response.llm_output['token_usage']`` or
    on individual generations. We do best-effort extraction; missing usage
    is fine.
    """
    if response is None:
        return {}
    llm_output = getattr(response, "llm_output", None) or {}
    usage = (llm_output.get("token_usage") if isinstance(llm_output, dict) else None) or {}
    out: dict[str, Any] = {}
    if "prompt_tokens" in usage:
        out["input_tokens"] = usage["prompt_tokens"]
    if "input_tokens" in usage:
        out["input_tokens"] = usage["input_tokens"]
    if "completion_tokens" in usage:
        out["output_tokens"] = usage["completion_tokens"]
    if "output_tokens" in usage:
        out["output_tokens"] = usage["output_tokens"]
    if "total_tokens" in usage:
        out["total_tokens"] = usage["total_tokens"]
    return out


def _serialize_llm_response(response: Any) -> Any:
    """Best-effort JSON-friendly form of an ``LLMResult``."""
    return _to_jsonable(response)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
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
