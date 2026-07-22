"""Framework adapters — callback handlers registered with agent frameworks.

The framework already maintains parent-child relationships internally and
fires lifecycle hooks at the right moments (``on_chain_start``,
``on_tool_start``, ``on_llm_end``, ``on_agent_action``). The adapter
translates those hooks into span builder calls.

- :mod:`.langgraph` — works for any LangChain-based runnable, including
  LangGraph. Passed to ``graph.invoke(config={"callbacks": [handler]})``.
  Requires the ``[langgraph]`` extra. Suppresses the SDK wrappers during
  model calls so a single trajectory has exactly one ``model_call`` span
  per LLM invocation.

- :mod:`.crewai` — for CrewAI (1.14+). Different registration model: CrewAI
  uses a process-global event bus, so **instantiating** the listener
  auto-registers it (no passing to ``kickoff``). Requires the ``[crewai]``
  extra. Correlation uses CrewAI's built-in ``event_id`` / ``parent_event_id``
  / ``started_event_id`` block — the same shape as LangChain's run ids. The
  crewai-free translation logic lives in :mod:`._crewai_emitter` so it can be
  unit-tested without the extra installed.

``OpenAI Agents SDK`` adapter follows. AutoGen / Pydantic AI as demand
requires.
"""
