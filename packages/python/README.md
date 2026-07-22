# agent-capture (Python)

The Python SDK for capturing AI agent trajectories. See the
[repository root](../../README.md) and
[architecture doc](../../docs/architecture.md) for context.

## Install

```bash
uv add agent-capture
# or with optional integrations:
uv add 'agent-capture[anthropic,langgraph,redaction]'
```

## Minimal example

```python
from agent_capture import traced
from agent_capture.exporter import FileExporter, configure

configure(exporter=FileExporter("trajectory.jsonl"))

@traced(type="retrieval")
def fetch_credit_report(applicant_id: str) -> dict:
    ...
```

## Status

Pre-alpha. Following the six-week build sequence in
[`docs/architecture.md` §12](../../docs/architecture.md).
