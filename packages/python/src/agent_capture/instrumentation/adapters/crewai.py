"""CrewAI framework adapter.

Register :class:`CrewAICaptureListener` once and it captures a crew's full
trajectory automatically. Unlike the LangGraph adapter (which is passed to
``graph.invoke(callbacks=[...])``), CrewAI uses a process-global event bus:
**instantiating** a ``BaseEventListener`` subclass auto-registers its
handlers. Keep the instance alive (module level)::

    from agent_capture import configure
    from agent_capture.instrumentation.adapters.crewai import CrewAICaptureListener

    configure(exporter=..., default_compliance=...)
    _listener = CrewAICaptureListener()   # registers with the global bus

    crew.kickoff(inputs=...)              # now produces a trajectory

Event → span mapping (core + bonus):

    CrewKickoff*          -> planner_step (trajectory root)
    Task*                 -> planner_step
    AgentExecution*       -> sub_agent_invocation
    ToolUsage*            -> tool_call
    LLMCall*              -> model_call   (model, messages, usage dict)
    HumanFeedbackReceived -> human_approval   (bonus, point span)
    KnowledgeRetrieval* / MemoryQuery*  -> retrieval   (bonus)

Not mapped (no native CrewAI event): ``side_effect`` and ``policy_check`` —
use the manual ``@traced`` decorator for those.

The translation logic lives in
:class:`agent_capture.instrumentation.adapters._crewai_emitter._SpanEmitter`,
which is crewai-free and unit-tested with synthetic events. This module is the
thin ``BaseEventListener`` shell that wires CrewAI's event classes to it, and
it imports ``crewai`` lazily so the rest of the SDK doesn't require the extra.

Dedup note: CrewAI routes model calls through litellm and emits its own
``LLMCall*`` events, so the model_call span comes from the event stream, not
from a wrapped provider client. The SDK-wrapper suppress flag is therefore not
applicable (events are observed out-of-band, not in the call stack) and is
intentionally not used.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent_capture._internal.runtime import default_builder
from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.instrumentation.adapters._crewai_emitter import _SpanEmitter
from agent_capture.span.builder import SpanBuilder

if TYPE_CHECKING:
    from crewai.events import BaseEventListener as _BaseEventListener
else:
    try:
        from crewai.events import BaseEventListener as _BaseEventListener
    except ImportError as exc:  # pragma: no cover — exercised by users who skip the extra
        raise ImportError(
            "agent_capture.instrumentation.adapters.crewai requires the `crewai` "
            "extra. Install with: pip install 'agent-capture[crewai]'"
        ) from exc


class CrewAICaptureListener(_BaseEventListener):
    """CrewAI ``BaseEventListener`` that emits agent-capture spans.

    Instantiate once at module level, after :func:`agent_capture.configure`::

        _listener = CrewAICaptureListener()

    Construction registers the handlers with CrewAI's global event bus
    (``BaseEventListener.__init__`` calls ``setup_listeners``). The instance
    must stay referenced to remain active.

    Args:
        builder: Override builder; defaults to the process-wide one set by
            :func:`agent_capture.configure`.
    """

    def __init__(self, *, builder: SpanBuilder | None = None) -> None:
        self._emitter = _SpanEmitter(builder or default_builder())
        super().__init__()  # triggers setup_listeners(crewai_event_bus)

    def setup_listeners(self, crewai_event_bus: Any) -> None:
        from crewai.events import (
            CrewKickoffCompletedEvent,
            CrewKickoffFailedEvent,
            CrewKickoffStartedEvent,
            LLMCallCompletedEvent,
            LLMCallFailedEvent,
            LLMCallStartedEvent,
            TaskCompletedEvent,
            TaskFailedEvent,
            TaskStartedEvent,
            ToolUsageErrorEvent,
            ToolUsageFinishedEvent,
            ToolUsageStartedEvent,
        )
        from crewai.events.types.agent_events import (
            AgentExecutionCompletedEvent,
            AgentExecutionErrorEvent,
            AgentExecutionStartedEvent,
        )

        e = self._emitter

        @crewai_event_bus.on(CrewKickoffStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_crew_start(ev)

        @crewai_event_bus.on(CrewKickoffCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_crew_end(ev)

        @crewai_event_bus.on(CrewKickoffFailedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_crew_error(ev)

        @crewai_event_bus.on(TaskStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_task_start(ev)

        @crewai_event_bus.on(TaskCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_task_end(ev)

        @crewai_event_bus.on(TaskFailedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_task_error(ev)

        @crewai_event_bus.on(AgentExecutionStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_agent_start(ev)

        @crewai_event_bus.on(AgentExecutionCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_agent_end(ev)

        @crewai_event_bus.on(AgentExecutionErrorEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_agent_error(ev)

        @crewai_event_bus.on(ToolUsageStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_tool_start(ev)

        @crewai_event_bus.on(ToolUsageFinishedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_tool_end(ev)

        @crewai_event_bus.on(ToolUsageErrorEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_tool_error(ev)

        @crewai_event_bus.on(LLMCallStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_llm_start(ev)

        @crewai_event_bus.on(LLMCallCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_llm_end(ev)

        @crewai_event_bus.on(LLMCallFailedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_llm_error(ev)

        self._register_bonus(crewai_event_bus)

    def _register_bonus(self, crewai_event_bus: Any) -> None:
        """Register bonus mappings; tolerant of event classes moving across versions."""
        e = self._emitter
        try:
            from crewai.events import (
                HumanFeedbackReceivedEvent,
                KnowledgeRetrievalCompletedEvent,
                KnowledgeRetrievalStartedEvent,
                MemoryQueryCompletedEvent,
                MemoryQueryFailedEvent,
                MemoryQueryStartedEvent,
            )
        except ImportError as exc:  # pragma: no cover
            log_error(
                ErrorCode.AC204,
                "crewai adapter: bonus events unavailable in this version: %s",
                exc,
                exc_info=False,
            )
            return

        @crewai_event_bus.on(HumanFeedbackReceivedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_human_feedback_received(ev)

        @crewai_event_bus.on(KnowledgeRetrievalStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_retrieval_start(ev)

        @crewai_event_bus.on(KnowledgeRetrievalCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_retrieval_end(ev)

        @crewai_event_bus.on(MemoryQueryStartedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_retrieval_start(ev)

        @crewai_event_bus.on(MemoryQueryCompletedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_retrieval_end(ev)

        @crewai_event_bus.on(MemoryQueryFailedEvent)
        def _(_s: Any, ev: Any) -> None:
            e.on_retrieval_error(ev)
