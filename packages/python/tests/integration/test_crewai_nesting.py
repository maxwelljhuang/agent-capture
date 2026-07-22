"""CrewAI adapter tests.

The translation logic (`_SpanEmitter`) is crewai-free, so these tests run
without the `crewai` extra installed — they feed synthetic event objects that
mimic CrewAI 1.14.1's event fields (event_id / parent_event_id /
started_event_id correlation block, plus per-event payloads).

A separate gated test confirms the real `CrewAICaptureListener` registers with
the bus when `crewai` IS installed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent_capture.exporter.base import SpanExporter
from agent_capture.instrumentation.adapters._crewai_emitter import _SpanEmitter
from agent_capture.schema import (
    ComplianceMetadata,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.span.builder import SpanBuilder


class _Capture(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _builder() -> tuple[SpanBuilder, _Capture]:
    exp = _Capture()
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


def _ev(**fields: Any) -> SimpleNamespace:
    """Synthetic CrewAI event. Defaults the correlation block to None."""
    base = {
        "event_id": None,
        "parent_event_id": None,
        "started_event_id": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def test_full_sequential_crew_nests_correctly() -> None:
    """crew ▷ task ▷ agent ▷ (tool, llm) — wired by event_id/parent_event_id."""
    b, exp = _builder()
    em = _SpanEmitter(b)

    # ids
    crew, task, agent, tool, llm = "c", "t", "a", "to", "l"

    em.on_crew_start(_ev(event_id=crew, crew_name="research_crew", inputs={"topic": "x"}))
    em.on_task_start(_ev(event_id=task, parent_event_id=crew, task=SimpleNamespace(description="Write report")))
    em.on_agent_start(
        _ev(event_id=agent, parent_event_id=task, agent=SimpleNamespace(role="researcher"), task_prompt="go")
    )
    em.on_tool_start(_ev(event_id=tool, parent_event_id=agent, tool_name="web_search", tool_args={"q": "x"}))
    em.on_tool_end(_ev(started_event_id=tool, output="results"))
    em.on_llm_start(
        _ev(
            event_id=llm,
            parent_event_id=agent,
            model="anthropic/claude-opus-4-7",
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    em.on_llm_end(
        _ev(
            started_event_id=llm,
            response="done",
            usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        )
    )
    em.on_agent_end(_ev(started_event_id=agent, output="agent output"))
    em.on_task_end(_ev(started_event_id=task, output="task output"))
    em.on_crew_end(_ev(started_event_id=crew, output="final"))

    # Leaves ship first; crew root ships last.
    by_name = {s.name: s for s in exp.spans}
    assert {"research_crew", "Write report", "researcher", "web_search", "llm_call"} <= set(by_name)

    root = by_name["research_crew"]
    assert root.type is SpanType.PLANNER_STEP
    assert root.parent_span_id is None

    task_span = by_name["Write report"]
    assert task_span.parent_span_id == root.span_id

    agent_span = by_name["researcher"]
    assert agent_span.type is SpanType.SUB_AGENT_INVOCATION
    assert agent_span.parent_span_id == task_span.span_id

    tool_span = by_name["web_search"]
    assert tool_span.type is SpanType.TOOL_CALL
    assert tool_span.parent_span_id == agent_span.span_id

    llm_span = by_name["llm_call"]
    assert llm_span.type is SpanType.MODEL_CALL
    assert llm_span.parent_span_id == agent_span.span_id


def test_hash_chain_links_through_the_tree() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_agent_start(_ev(event_id="a", parent_event_id="c", agent=SimpleNamespace(role="solo")))
    em.on_agent_end(_ev(started_event_id="a", output="x"))
    em.on_crew_end(_ev(started_event_id="c", output="y"))

    by_id = {s.span_id: s for s in exp.spans}
    for s in exp.spans:
        if s.parent_span_id is None:
            assert s.provenance.parent_content_hash is None
        else:
            parent = by_id[s.parent_span_id]
            assert s.provenance.parent_content_hash == parent.provenance.content_hash


def test_llm_token_usage_captured_from_usage_dict() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_llm_start(_ev(event_id="l", parent_event_id="c", model="gpt-4o", messages="prompt"))
    em.on_llm_end(
        _ev(
            started_event_id="l", response="ans", usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        )
    )
    em.on_crew_end(_ev(started_event_id="c", output="z"))

    llm = next(s for s in exp.spans if s.type is SpanType.MODEL_CALL)
    assert llm.attributes.model_name == "gpt-4o"
    assert llm.attributes.provider == "litellm"
    assert llm.attributes.input_tokens == 7
    assert llm.attributes.output_tokens == 3
    assert llm.attributes.total_tokens == 10


def test_provider_parsed_from_namespaced_model() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_llm_start(_ev(event_id="l", parent_event_id="c", model="anthropic/claude-opus-4-7", messages="p"))
    em.on_llm_end(_ev(started_event_id="l", response="r"))
    em.on_crew_end(_ev(started_event_id="c", output="z"))
    llm = next(s for s in exp.spans if s.type is SpanType.MODEL_CALL)
    assert llm.attributes.provider == "anthropic"
    assert llm.attributes.model_name == "anthropic/claude-opus-4-7"


def test_tool_error_records_error_span() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_tool_start(_ev(event_id="to", parent_event_id="c", tool_name="flaky"))
    em.on_tool_error(_ev(started_event_id="to", error="boom"))
    em.on_crew_end(_ev(started_event_id="c", output="z"))

    tool = next(s for s in exp.spans if s.type is SpanType.TOOL_CALL)
    assert tool.status is SpanStatus.ERROR
    assert tool.error is not None
    assert tool.error.message == "boom"


def test_human_feedback_maps_to_human_approval() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_human_feedback_received(
        _ev(parent_event_id="c", method_name="review_step", feedback="looks good", outcome="approve")
    )
    em.on_crew_end(_ev(started_event_id="c", output="z"))

    ha = next(s for s in exp.spans if s.type is SpanType.HUMAN_APPROVAL)
    assert ha.attributes.decision == "approved"
    assert ha.parent_span_id is not None  # nested under the crew root


def test_knowledge_retrieval_maps_to_retrieval() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_retrieval_start(_ev(event_id="k", parent_event_id="c", source_type="knowledge", query="what is x"))
    em.on_retrieval_end(_ev(started_event_id="k", retrieved_knowledge="x is y"))
    em.on_crew_end(_ev(started_event_id="c", output="z"))

    r = next(s for s in exp.spans if s.type is SpanType.RETRIEVAL)
    assert r.attributes.query == "what is x"


def test_no_builder_is_safe_noop() -> None:
    em = _SpanEmitter(None)
    # Must not raise even with no builder configured.
    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_crew_end(_ev(started_event_id="c", output="z"))


def test_unbalanced_end_without_start_is_safe() -> None:
    b, exp = _builder()
    em = _SpanEmitter(b)
    # An end whose started_event_id we never saw → no-op, no raise.
    em.on_tool_end(_ev(started_event_id="never-seen", output="x"))
    assert exp.spans == []


def test_concurrent_crews_do_not_clobber_each_other() -> None:
    """Two crews interleaved on one (global-bus) listener must stay isolated.

    Regression: on_crew_end used to call self._open.clear(), which under
    CrewAI's process-global event bus would wipe a concurrently-running
    crew's still-open spans. CrewAI's per-event parent_event_id /
    started_event_id correlation isolates runs without any blanket cleanup.
    """
    b, exp = _builder()
    em = _SpanEmitter(b)

    # Crew A and Crew B start, interleaved on the shared listener.
    em.on_crew_start(_ev(event_id="A", crew_name="crew_a"))
    em.on_crew_start(_ev(event_id="B", crew_name="crew_b"))
    em.on_agent_start(_ev(event_id="B-agent", parent_event_id="B", agent=SimpleNamespace(role="b_agent")))

    # Crew A finishes first — must NOT wipe crew B's open spans.
    em.on_crew_end(_ev(started_event_id="A", output="a done"))

    # Crew B's agent still closes and stays parented under crew B.
    em.on_agent_end(_ev(started_event_id="B-agent", output="b agent done"))
    em.on_crew_end(_ev(started_event_id="B", output="b done"))

    by_name = {s.name: s for s in exp.spans}
    assert {"crew_a", "crew_b", "b_agent"} <= set(by_name)
    assert by_name["b_agent"].parent_span_id == by_name["crew_b"].span_id
    assert by_name["b_agent"].provenance.parent_content_hash == by_name["crew_b"].provenance.content_hash


def test_emitter_is_thread_safe_under_concurrent_handlers() -> None:
    """CrewAI dispatches handlers on a background thread pool, so the emitter
    is touched concurrently. Regression for the missing lock that produced
    non-deterministic span counts and dangling parents on the real demo:
    many LLM children opened+closed concurrently under one shared agent must
    all ship, all parented to the agent, with no drops and no dangling.
    """
    import threading

    b, exp = _builder()
    em = _SpanEmitter(b)

    em.on_crew_start(_ev(event_id="c", crew_name="crew"))
    em.on_task_start(_ev(event_id="t", parent_event_id="c", task=SimpleNamespace(description="task")))
    em.on_agent_start(_ev(event_id="a", parent_event_id="t", agent=SimpleNamespace(role="agent")))

    n = 30
    barrier = threading.Barrier(n)

    def child(i: int) -> None:
        barrier.wait()  # maximize contention — all threads hit open/close together
        em.on_llm_start(_ev(event_id=f"llm{i}", parent_event_id="a", model="gpt-4o-mini", messages="m"))
        em.on_llm_end(
            _ev(
                started_event_id=f"llm{i}",
                response="r",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        )

    threads = [threading.Thread(target=child, args=(i,)) for i in range(n)]
    for thr in threads:
        thr.start()
    for thr in threads:
        thr.join()

    em.on_agent_end(_ev(started_event_id="a", output="o"))
    em.on_task_end(_ev(started_event_id="t", output="o"))
    em.on_crew_end(_ev(started_event_id="c", output="o"))

    # crew + task + agent + n llm calls, none dropped, none dangling.
    assert len(exp.spans) == 3 + n
    ids = {s.span_id for s in exp.spans}
    assert all(s.parent_span_id is None or s.parent_span_id in ids for s in exp.spans)
    llm_spans = [s for s in exp.spans if s.type is SpanType.MODEL_CALL]
    assert len(llm_spans) == n
    agent = next(s for s in exp.spans if s.type is SpanType.SUB_AGENT_INVOCATION)
    assert all(s.parent_span_id == agent.span_id for s in llm_spans)


# ---- gated test: the real listener registers when crewai is installed ----


def test_real_listener_registers_when_crewai_present() -> None:
    pytest.importorskip("crewai")
    from agent_capture.instrumentation.adapters.crewai import CrewAICaptureListener

    b, _ = _builder()
    listener = CrewAICaptureListener(builder=b)
    # If construction didn't raise, setup_listeners ran and registered handlers.
    assert listener._emitter is not None
