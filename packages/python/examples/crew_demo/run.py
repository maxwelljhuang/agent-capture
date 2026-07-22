#!/usr/bin/env python3
"""Minimal real-CrewAI demo — validates the agent-capture CrewAI adapter.

A tiny sequential crew (mirroring the shape of a real scheduling crew):

    intake  ->  lookup (with a stubbed tool)  ->  synthesizer

It makes real OpenAI calls via CrewAI's native ``LLM`` (so CrewAI emits its
``LLMCall*`` events, which the adapter turns into ``model_call`` spans). No
database, no Kafka — just enough to confirm the adapter speaks CrewAI 1.14.x
correctly and that redaction fires on real captured data.

Run::

    pip install -e packages/python
    pip install -r packages/python/examples/crew_demo/requirements.txt
    export OPENAI_API_KEY=sk-...
    export AGENT_CAPTURE_HMAC_KEY=demo-key
    python packages/python/examples/crew_demo/run.py

Then verify (as a separate command, after this process exits)::

    python scripts/verify_trajectory.py trajectory.jsonl
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tools import BaseTool

from agent_capture import configure
from agent_capture.exporter import FileExporter
from agent_capture.instrumentation.adapters.crewai import CrewAICaptureListener
from agent_capture.redaction import RedactionFilter, load_policy
from agent_capture.schema import ComplianceMetadata
from agent_capture.schema.compliance import (
    DataClassification,
    RegulatoryRegime,
    RetentionClass,
)


class LookupStudentTool(BaseTool):
    """Stubbed student lookup — canned data, no database."""

    name: str = "lookup_student"
    description: str = (
        "Look up a student by first name. Returns a JSON record with id, "
        "grade, and enrolled subjects, or a not-found note."
    )

    def _run(self, student_name: str) -> str:
        canned = {
            "Maya": {"id": 4021, "grade": 7, "subjects": ["algebra", "english"]},
        }
        first = (student_name or "").strip().split()[0].title() if student_name else ""
        return json.dumps(canned.get(first, {"error": "student not found"}))


def build_crew() -> Crew:
    llm = LLM(model="gpt-4o-mini", temperature=0)
    lookup_tool = LookupStudentTool()

    intake = Agent(
        role="Intake Specialist",
        goal="Extract the subject, student name, and requested date from the parent's message.",
        backstory="You turn a parent's natural-language scheduling request into structured fields.",
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    lookup = Agent(
        role="Lookup Specialist",
        goal="Resolve the student to a record using the lookup_student tool.",
        backstory="You translate a student's name into their record.",
        tools=[lookup_tool],
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )
    synthesizer = Agent(
        role="Reply Synthesizer",
        goal="Write a short, friendly confirmation reply to the parent.",
        backstory="You compose the final message back to the parent.",
        llm=llm,
        allow_delegation=False,
        verbose=False,
    )

    extract_task = Task(
        description=(
            "From the parent's message below, extract a compact JSON object with "
            "keys subject, student_name, requested_date. Use null for anything "
            "missing.\n\nMessage:\n{message}"
        ),
        expected_output="A JSON object with subject, student_name, requested_date.",
        agent=intake,
    )
    lookup_task = Task(
        description=(
            "Call lookup_student with the student_name from the previous task and report the returned record."
        ),
        expected_output="The student record JSON or a not-found note.",
        agent=lookup,
        context=[extract_task],
    )
    synth_task = Task(
        description=(
            "Write a single-sentence reply confirming you'll schedule the requested "
            "tutoring session. Do not include any personal identifiers."
        ),
        expected_output="One sentence.",
        agent=synthesizer,
        context=[extract_task, lookup_task],
    )

    return Crew(
        agents=[intake, lookup, synthesizer],
        tasks=[extract_task, lookup_task, synth_task],
        process=Process.sequential,
        verbose=False,
    )


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("Set OPENAI_API_KEY before running this demo.", file=sys.stderr)
        return 2
    os.environ.setdefault("AGENT_CAPTURE_HMAC_KEY", "demo-key")

    here = Path(__file__).parent
    out_path = Path("trajectory.jsonl").resolve()
    out_path.unlink(missing_ok=True)

    configure(
        exporter=FileExporter(out_path),
        default_compliance=ComplianceMetadata(
            policy_version_active="demo-v1",
            agent_version="crew-demo@0.1",
            end_customer_id="demo-co",
            regulatory_regime=[RegulatoryRegime.GDPR],
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.PII,
        ),
        redaction_filter=RedactionFilter(policy=load_policy(here / "policy.yaml")),
    )
    # Registers with CrewAI's process-global event bus. Keep the reference alive.
    _listener = CrewAICaptureListener()

    crew = build_crew()
    result = crew.kickoff(
        inputs={
            # Seeded PII: the SSN (free text) is caught by the pattern recognizer;
            # parent_email / parent_phone (structured keys) by field-name rules.
            "message": ("Please schedule algebra tutoring for Maya next Tuesday. My SSN is 123-45-6789."),
            "parent_email": "jane@example.com",
            "parent_phone": "415-555-0123",
        }
    )

    # CrewAI dispatches some completed-event handlers on a background pool;
    # give them a moment to drain so trajectory.jsonl is complete before exit.
    time.sleep(2.0)

    print(f"Crew result: {str(result)[:200]}")
    print(f"Wrote trajectory to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
