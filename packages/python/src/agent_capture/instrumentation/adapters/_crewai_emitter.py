"""CrewAI-free span translation logic for the CrewAI adapter.

This module deliberately does NOT import ``crewai`` so its logic can be unit
tested with synthetic event objects on a machine without the ``crewai`` extra
installed. :mod:`agent_capture.instrumentation.adapters.crewai` holds the thin
``BaseEventListener`` shell that forwards bus callbacks here.

Correlation (CrewAI 1.14.1): every event inherits ``event_id``,
``parent_event_id`` (the enclosing scope's id), and — on completed/failed
events — ``started_event_id`` (the matching started event's id). The emitter
keys open spans by ``event_id``, resolves parents via ``parent_event_id``, and
closes via ``started_event_id``.
"""

from __future__ import annotations

import threading
from typing import Any

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.schema import ErrorInfo, SpanStatus, SpanType
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    RetrievalAttributes,
    SubAgentInvocationAttributes,
    ToolCallAttributes,
    TypedAttributes,
)
from agent_capture.span.builder import OpenSpan, SpanBuilder


class _SpanEmitter:
    """Translate CrewAI events into agent-capture spans.

    Holds the ``event_id -> OpenSpan`` map and the builder. All methods take
    duck-typed event objects (anything exposing the CrewAI event fields), so
    this class is unit-testable with synthetic events.

    Thread-safety: CrewAI dispatches event handlers on a background thread
    pool, so the same emitter is touched from multiple threads. A single
    re-entrant lock serializes every open/close so the ``_open`` map and the
    builder's parent-buffering can't be corrupted by concurrent handlers.
    CrewAI emits events in correct nested order (start→child→…→end); the lock
    preserves that by making each handler's span op atomic.
    """

    def __init__(self, builder: SpanBuilder | None) -> None:
        self._builder = builder
        self._open: dict[str, OpenSpan] = {}
        self._lock = threading.RLock()

    # ---- low-level open/close ------------------------------------------

    def _start(
        self,
        *,
        event_id: str | None,
        parent_event_id: str | None,
        name: str,
        type: SpanType,
        attributes: TypedAttributes,
        inputs: Any | None = None,
    ) -> None:
        if self._builder is None or not event_id:
            return
        with self._lock:
            parent = self._open.get(parent_event_id) if parent_event_id else None
            try:
                open_span = self._builder.open(
                    name=name,
                    type=type,
                    attributes=attributes,
                    inputs=inputs,
                    parent=parent,
                )
            except Exception as exc:
                log_error(ErrorCode.AC204, "crewai adapter open failed: %s", exc)
                return
            self._open[event_id] = open_span

    def _end(
        self,
        *,
        started_event_id: str | None,
        outputs: Any | None = None,
        attr_update: dict[str, Any] | None = None,
    ) -> None:
        if self._builder is None or not started_event_id:
            return
        with self._lock:
            open_span = self._open.pop(started_event_id, None)
            if open_span is None:
                return
            if attr_update:
                try:
                    open_span.attributes = open_span.attributes.model_copy(update=attr_update)
                except Exception as exc:
                    log_error(ErrorCode.AC203, "crewai adapter attribute update failed: %s", exc)
            try:
                self._builder.close(open_span, outputs=outputs)
            except Exception as exc:
                log_error(ErrorCode.AC205, "crewai adapter close failed: %s", exc)

    def _error(self, *, started_event_id: str | None, message: str) -> None:
        if self._builder is None or not started_event_id:
            return
        with self._lock:
            open_span = self._open.pop(started_event_id, None)
            if open_span is None:
                return
            try:
                self._builder.close(
                    open_span,
                    status=SpanStatus.ERROR,
                    error=ErrorInfo(error_type="crewai.error", message=message),
                )
            except Exception as exc:
                log_error(ErrorCode.AC206, "crewai adapter close-with-error failed: %s", exc)

    # ---- per-event handlers (read event fields) -------------------------

    # Crew -> planner_step (root)
    def on_crew_start(self, event: Any) -> None:
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name=_get(event, "crew_name") or "crew",
            type=SpanType.PLANNER_STEP,
            attributes=PlannerStepAttributes(),
            inputs=_to_jsonable(_get(event, "inputs")),
        )

    def on_crew_end(self, event: Any) -> None:
        # No blanket cleanup of self._open: this listener is registered on
        # CrewAI's process-global event bus, so concurrent crew runs (e.g. a
        # Kafka consumer processing several messages at once) share this map.
        # Clearing it here would wipe other in-flight crews' open spans.
        # CrewAI's per-event parent_event_id already isolates runs, and the
        # matching ``started_event_id`` pops each span on its own end event.
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs=_to_jsonable(_get(event, "output")),
        )

    def on_crew_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "crew kickoff failed"),
        )

    # Task -> planner_step
    def on_task_start(self, event: Any) -> None:
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name=_describe_task(_get(event, "task")) or _get(event, "task_name") or "task",
            type=SpanType.PLANNER_STEP,
            attributes=PlannerStepAttributes(),
            inputs=_to_jsonable(_get(event, "context")),
        )

    def on_task_end(self, event: Any) -> None:
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs=_to_jsonable(_get(event, "output")),
        )

    def on_task_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "task failed"),
        )

    # Agent -> sub_agent_invocation
    def on_agent_start(self, event: Any) -> None:
        role = _get(_get(event, "agent"), "role") or _get(event, "agent_role") or "agent"
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name=str(role),
            type=SpanType.SUB_AGENT_INVOCATION,
            attributes=SubAgentInvocationAttributes(sub_agent_identity=str(role)),
            inputs=_to_jsonable(_get(event, "task_prompt")),
        )

    def on_agent_end(self, event: Any) -> None:
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs=_to_jsonable(_get(event, "output")),
        )

    def on_agent_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "agent execution error"),
        )

    # Tool -> tool_call
    def on_tool_start(self, event: Any) -> None:
        name = _get(event, "tool_name") or "tool"
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name=str(name),
            type=SpanType.TOOL_CALL,
            attributes=ToolCallAttributes(tool_name=str(name)),
            inputs={"args": _to_jsonable(_get(event, "tool_args"))},
        )

    def on_tool_end(self, event: Any) -> None:
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs={"output": _to_jsonable(_get(event, "output"))},
        )

    def on_tool_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "tool usage error"),
        )

    # LLM -> model_call
    def on_llm_start(self, event: Any) -> None:
        model = _get(event, "model")
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name="llm_call",
            type=SpanType.MODEL_CALL,
            attributes=ModelCallAttributes(
                model_name=str(model) if model else "unknown",
                provider=_provider_from_model(model),
            ),
            inputs={"messages": _to_jsonable(_get(event, "messages"))},
        )

    def on_llm_end(self, event: Any) -> None:
        usage = _get(event, "usage") or {}
        update: dict[str, Any] = {}
        if isinstance(usage, dict) and usage:
            update = {
                "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
                "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs=_to_jsonable(_get(event, "response")),
            attr_update=update or None,
        )

    def on_llm_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "llm call failed"),
        )

    # Human feedback -> human_approval (bonus, point span on Received)
    def on_human_feedback_received(self, event: Any) -> None:
        if self._builder is None:
            return
        ts = _get(event, "timestamp")
        attrs = HumanApprovalAttributes(
            approver_identity="human",
            approver_role="reviewer",
            decision=_map_decision(_get(event, "outcome")),
            decision_timestamp=ts.isoformat() if hasattr(ts, "isoformat") else "1970-01-01T00:00:00Z",
            artifact_reviewed=str(_get(event, "method_name") or "feedback"),
        )
        parent_event_id = _get(event, "parent_event_id")
        with self._lock:
            parent = self._open.get(parent_event_id) if parent_event_id else None
            try:
                open_span = self._builder.open(
                    name="human_feedback",
                    type=SpanType.HUMAN_APPROVAL,
                    attributes=attrs,
                    parent=parent,
                )
                self._builder.close(open_span, outputs={"feedback": _to_jsonable(_get(event, "feedback"))})
            except Exception as exc:
                log_error(ErrorCode.AC204, "crewai adapter human_approval failed: %s", exc)

    # Knowledge / Memory -> retrieval (bonus)
    def on_retrieval_start(self, event: Any) -> None:
        self._start(
            event_id=_get(event, "event_id"),
            parent_event_id=_get(event, "parent_event_id"),
            name="retrieval",
            type=SpanType.RETRIEVAL,
            attributes=RetrievalAttributes(
                source_identifier=str(_get(event, "source_type") or "knowledge"),
                query=_str_or_none(_get(event, "query")),
            ),
        )

    def on_retrieval_end(self, event: Any) -> None:
        self._end(
            started_event_id=_get(event, "started_event_id"),
            outputs={"retrieved": _to_jsonable(_get(event, "retrieved_knowledge") or _get(event, "results"))},
        )

    def on_retrieval_error(self, event: Any) -> None:
        self._error(
            started_event_id=_get(event, "started_event_id"),
            message=str(_get(event, "error") or "retrieval failed"),
        )


# ---- module-private helpers ----------------------------------------------


def _get(obj: Any, attr: str) -> Any:
    """Attribute access tolerant of missing fields and dict-shaped events."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(attr)
    return getattr(obj, attr, None)


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _describe_task(task: Any) -> str | None:
    if task is None:
        return None
    desc = _get(task, "description")
    if desc:
        return str(desc).strip().splitlines()[0][:80]
    return None


def _provider_from_model(model: Any) -> str:
    """Litellm model strings look like 'gpt-4o' or 'anthropic/claude-...'."""
    if not model:
        return "litellm"
    s = str(model)
    return s.split("/", 1)[0] if "/" in s else "litellm"


def _map_decision(outcome: Any) -> str:
    if not outcome:
        return "approved"
    low = str(outcome).lower()
    if "reject" in low or "deny" in low:
        return "rejected"
    if "escalat" in low:
        return "escalated"
    return "approved"


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
