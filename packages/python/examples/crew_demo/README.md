# CrewAI demo crew

The smallest real CrewAI run that proves the `agent-capture` CrewAI adapter
works against a live runtime (not stubbed events). It's both a validation
harness and the canonical "how to instrument a CrewAI crew" example.

A sequential crew — `intake → lookup → synthesizer` — makes real OpenAI calls
through CrewAI's native `LLM`, with the recorder attached. No database, no
Kafka: just `crewai` + `openai` + the local SDK.

## Run

```bash
# from the repo root, in a throwaway venv
python -m venv .venv && source .venv/bin/activate
pip install -e packages/python
pip install -r packages/python/examples/crew_demo/requirements.txt

export OPENAI_API_KEY=sk-...
export AGENT_CAPTURE_HMAC_KEY=demo-key

python packages/python/examples/crew_demo/run.py
```

Cost: a few cents on `gpt-4o-mini`.

## Verify

After the run finishes (run this as a *separate* command — the CrewAI event
bus flushes its handlers at process exit):

```bash
python scripts/verify_trajectory.py trajectory.jsonl
```

Expect `OK`. Then eyeball `trajectory.jsonl`:

- **Span types present:** `planner_step` (the crew + 3 tasks),
  `sub_agent_invocation` (the 3 agents), `tool_call` (`lookup_student`),
  `model_call` (with non-null `input_tokens` / `output_tokens`).
- **Redaction fired:** `123-45-6789` does **not** appear anywhere;
  `[REDACTED:ssn]` does; the `parent_email` / `parent_phone` values are
  `[FP:…]` HMAC fingerprints.
- **Hash chain links:** the verifier already asserts every child's
  `parent_content_hash` equals its parent's `content_hash`.

## What this proves (and doesn't)

It proves **our half of the contract**: the adapter correctly translates a
real CrewAI 1.14.x event stream into a complete, redacted, hash-chained
trajectory.

It does **not** prove the integration in your full service — that test lives
in your own app's dev/staging environment, where you add `agent-capture` as a
dependency and instantiate `CrewAICaptureListener()` once at startup. The
recorder is a guest inside your process; you run the service, it records.

## Wiring it into a real app

Three lines at startup, after your imports:

```python
from agent_capture import configure
from agent_capture.exporter import FileExporter
from agent_capture.instrumentation.adapters.crewai import CrewAICaptureListener

configure(exporter=FileExporter("trajectory.jsonl"), default_compliance=...)
_listener = CrewAICaptureListener()   # keep the reference alive
```

Every `crew.kickoff()` in the process then produces a trajectory. For
production, swap `FileExporter` for the HTTP exporter behind a
`BoundedQueueExporter`, and supply a real redaction policy.
