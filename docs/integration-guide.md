# Integration guide

This guide takes you from `pip install` to your first trajectory file in
under five minutes, then layers on the production pieces (LangGraph,
redaction, vendor-cloud HTTP ledger) one at a time.

Each section ends with a checkpoint you can verify in your shell.

---

## 0. What you'll host & provide (read this first)

`agent-capture` is **a library you install plus a service you host.** The SDK
runs inside your agent (no infra of its own) and ships everything it captures
to a **ledger service you run in your own cloud.** Full inventory below.

### The deployment picture

```
 YOUR AGENT PROCESS                       YOUR CLOUD (services you host)
 ┌────────────────────────┐               ┌─────────────────────────────┐
 │ agent + agent-capture  │  HTTPS spans  │ ledger service (FastAPI)    │
 │ SDK  (pip install)     │ ────────────▶ │   + Postgres 16  ◀── store  │
 │  • no database         │               │   + Ed25519 signing key     │
 │  • redaction in-process│               │   + API tokens              │
 └────────────────────────┘               ├─────────────────────────────┤
                                          │ (optional) enforcement svc  │
                                          │   + its own Postgres        │
                                          └─────────────────────────────┘
```

Everything runs in your cloud (SaaS) — the bank's data is already with you
because your agent processes it.

### Resources you provide

| Resource | What you do | Why it's needed |
|---|---|---|
| **SDK** | `pip install agent-capture` in your agent — **no infra** | Captures the trajectory in-process |
| **Ledger host** | A container/VM running `ledger serve` (+ `worker anchor`, `worker retention`) | The durable, tamper-evident store |
| **Postgres 16** (ledger) | One database — managed (RDS / Cloud SQL) or self-run | **This is where all trajectory data lives** |
| **Ed25519 signing key** | Generate + hold it (`openssl genpkey -algorithm ED25519`) | Signs the Merkle attestations regulators verify |
| **API tokens** | Mint via `ledger token create` — an `ingest` token for the SDK, a `reader` token for the dashboard | Authn + per-tenant (`end_customer_id`) scoping |
| **TLS ingress** | Terminate TLS in front of the service | The service binds plaintext by design |
| **HMAC key** | Set `AGENT_CAPTURE_HMAC_KEY` in the agent env (customer-managed / BYOK) | Redaction fingerprinting |
| **Redaction policy** | `policy.yaml`, authored by the customer's security team (§5) | Defines what's redacted before anything is persisted |
| **Enforcement service** *(optional)* | A container running `enforcement serve` + `timeout-worker`, **plus its own Postgres** | Blocking / hold-for-review on side effects |

### Who owns what
**You** host the ledger, its Postgres, and (optionally) enforcement, and hold
the signing key + tokens. **Your customer (the bank)** authors the redaction +
enforcement policies and manages the BYOK HMAC key — the data is theirs.

> **Just kicking the tires?** You can skip every service above: §2 writes
> trajectories to a local JSON-lines file with **zero infrastructure**. Stand
> up the ledger only when you're ready for the durable record (§6). Full host
> setup — Postgres, roles, keys, tokens, TLS — is in **`docs/deployment.md`**;
> for a fast Docker-Compose provision (ledger + Postgres in one command), see
> **`docs/tester-quickstart.md`**.

---

## 1. Install (30 seconds)

```bash
pip install agent-capture
# or, for the extras you'll need below:
pip install 'agent-capture[anthropic,langgraph,redaction]'
```

Supported Python: 3.11+.

---

## 2. First trajectory — file destination (3 minutes)

The smallest integration: one `configure()` call at process startup, one
`@traced` decorator on a function, one JSON-lines file you can inspect.

```python
# my_agent.py
from agent_capture import configure, traced
from agent_capture.exporter import FileExporter
from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.compliance import (
    DataClassification, RegulatoryRegime, RetentionClass,
)

configure(
    exporter=FileExporter("trajectory.jsonl"),
    default_compliance=ComplianceMetadata(
        policy_version_active="dev-v1",
        agent_version="my-agent@0.1.0",
        end_customer_id="acme-bank",
        regulatory_regime=[RegulatoryRegime.ECOA],
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    ),
)


@traced(type=SpanType.RETRIEVAL, name="fetch_credit_report")
def fetch_credit_report(applicant_id: str) -> dict:
    return {"score": 700}


@traced(type=SpanType.PLANNER_STEP, name="underwrite")
def underwrite() -> str:
    fetch_credit_report("app-9001")
    return "approve"


if __name__ == "__main__":
    print(underwrite())
```

**Checkpoint:**

```bash
python my_agent.py
cat trajectory.jsonl | head -1 | python -m json.tool
```

You should see a span object with `span_id`, `trajectory_id`,
`content_hash`, and your compliance metadata.

### Recording inputs/outputs on a context-manager scope

When you use `traced` as a context manager (instead of a decorator on a
function), pass `inputs=` at construction and call `set_outputs()`
before the block ends:

```python
scope = traced(type=SpanType.MODEL_CALL, name="score", inputs={"prompt": p})
with scope:
    response = client.messages.create(...)
    scope.set_outputs({"text": response.content[0]["text"]})
```

The decorator form captures both automatically from a function's args
and return value; the CM form needs the explicit hooks. The
`packages/python/examples/loan_denial/run.py` demo uses this pattern
for its `model_call` site.

---

## 3. Capture model calls automatically — SDK wrappers (1 minute)

Wrap your Anthropic or OpenAI client at construction. Every API call
through it produces a `model_call` span without any further decoration.

```python
from anthropic import Anthropic
from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap as wrap_anthropic

client = wrap_anthropic(Anthropic())
# Every client.messages.create(...) now produces a model_call span
# under the currently-active @traced scope.
```

OpenAI:

```python
from openai import OpenAI
from agent_capture.instrumentation.sdk_wrappers.openai import wrap as wrap_openai

client = wrap_openai(OpenAI())
```

Async clients work the same way (`AsyncAnthropic`, `AsyncOpenAI`) — the
wrapper auto-detects.

### LiteLLM — instrument at the LLM client, not the agent class

If your calls go through **LiteLLM** (a unified client over 100+ providers),
register the capture callback once:

```bash
pip install 'agent-capture[litellm]'
```
```python
from agent_capture.instrumentation.adapters.litellm import install
install()   # every litellm.completion()/acompletion()/Router call now emits a model_call span
```

Each call produces a `model_call` span (model, provider, temperature, token
usage) nested under the active `@traced` scope — sync and async.

> **Instrument at the LLM client, not the agent class.** Orchestrators often
> call the LLM client **directly** for intent detection, routing, and RAG,
> bypassing your agent class — so instrumenting the agent class silently misses
> those calls (zero `model_call` spans for a normal turn). The LLM client is the
> one choke point every model call passes through.

---

## 4. LangGraph / LangChain — framework adapter (2 minutes)

Add the callback handler to your graph runnable and the SDK captures
every chain step, tool call, LLM invocation, and retriever automatically.

```python
from agent_capture.instrumentation.adapters.langgraph import CaptureCallbackHandler

handler = CaptureCallbackHandler()
result = graph.invoke(state, config={"callbacks": [handler]})
```

The adapter is compatible with anything that uses LangChain callbacks
(LangGraph, LangChain runnables, agents).

**Dedup:** when the LangGraph adapter and a wrapped SDK client are both
active during the same LLM call, only one `model_call` span is emitted
(the framework-owned one). No configuration needed.

---

## 4b. CrewAI — framework adapter (2 minutes)

CrewAI (1.14+) uses a process-global event bus instead of per-call
callbacks, so the integration is different from LangGraph: you
**instantiate** the listener once and it registers itself. There's
nothing to pass to `kickoff()`.

```bash
pip install 'agent-capture[crewai]'
```

```python
from agent_capture import configure
from agent_capture.instrumentation.adapters.crewai import CrewAICaptureListener

configure(exporter=..., default_compliance=..., redaction_filter=...)

# Instantiate once, at module level, AFTER configure(). Keep the reference
# alive — if it's garbage-collected it stops capturing.
_listener = CrewAICaptureListener()

# Run your crew normally; a trajectory is produced automatically.
crew.kickoff(inputs={...})
```

What's captured: crew & tasks → `planner_step`, each agent →
`sub_agent_invocation`, tool use → `tool_call`, LLM calls → `model_call`
(with token counts), plus human-feedback → `human_approval` and
knowledge/memory → `retrieval`.

**Not captured by the adapter:** `side_effect` and `policy_check` — CrewAI
has no native event for these. If your crew sends an email, moves money, or
runs a compliance rule you need recorded, wrap that function with `@traced`
(see §2) and it nests into the trajectory automatically.

**Scope:** v1 targets **sequential** crews. Hierarchical/parallel crews
(manager delegation, async tasks) work via CrewAI's built-in event
correlation but are less tested — verify the trajectory nesting if you use
them.

---

## 5. Redaction — customer-owned policy (3 minutes)

The end customer owns the redaction policy as a YAML file. The vendor
ships the SDK; the customer's security team writes the policy.

`policy.yaml`:

```yaml
version: "lending-v2.3.1"
default_strategy: full

strategies:
  full:
    type: full
  account_hash:
    type: hmac
    key_env: AGENT_CAPTURE_HMAC_KEY  # never inline the key here

field_rules:
  - field_name: ssn
    strategy: full
  - field_name: account_number
    strategy: account_hash

pattern_rules:
  - field_type: ssn
    strategy: full
  - field_type: routing_number
    strategy: account_hash
  - field_type: date_of_birth
    strategy: full
```

Wire it in at startup:

```python
from agent_capture.redaction import RedactionFilter, load_policy

policy = load_policy("policy.yaml")
configure(
    exporter=FileExporter("trajectory.jsonl"),
    default_compliance=default_compliance,
    redaction_filter=RedactionFilter(policy=policy),
)
```

After this, every span's `content_hash` is computed over the
post-redaction bytes. Sensitive values never leave the agent's memory
in plaintext.

**Checkpoint:**

```bash
AGENT_CAPTURE_HMAC_KEY=test-key python my_agent.py
grep -c '\[REDACTED:' trajectory.jsonl   # > 0 if redaction fired
grep -c '\[FP:' trajectory.jsonl          # > 0 if HMAC fired
```

---

## 6. Production destination — HTTP exporter (2 minutes)

For production, swap `FileExporter` for `HTTPExporter` and wrap it in a
`BoundedQueueExporter` so the agent's hot path never blocks on the
network.

> The ledger URL is the service you host (§0): `docs/deployment.md` is the
> runbook, `docs/tester-quickstart.md` brings it up locally. `auth_token` is the
> `ingest` token from `ledger token create --role ingest --customer <end_customer_id>`.

```python
from agent_capture.exporter import (
    BoundedQueueExporter,
    HTTPExporter,
    install_handlers,
)

ledger = HTTPExporter(
    "https://ledger.vendor-cloud.example/spans",
    auth_token=os.environ["AGENT_CAPTURE_LEDGER_TOKEN"],
    batch_size=100,
    batch_max_wait_s=1.0,
)
pipeline = BoundedQueueExporter(ledger, max_size=10_000)

configure(
    exporter=pipeline,
    default_compliance=default_compliance,
    redaction_filter=redaction,
)

# Graceful shutdown + crash-restart durability.
install_handlers(
    pipeline,
    drain_unshipped=lambda: [],   # see below
)
```

**Failure semantics** (architecture doc §9.2):

- Transient failures (5xx, network errors) → exponential backoff retry
- Permanent failures (4xx) → dropped with a loud `[AC403]` safelog entry
- Queue saturation → oldest non-critical spans dropped (`human_approval`
  and `side_effect` block briefly instead of being silently dropped)
- Never raises into the agent's code path

### Multi-tenant processes (one process, many tenants)

If a single process serves many tenants, stamp `end_customer_id = <tenant>` in
each span's compliance metadata, and ship through **one unscoped ingest token**:

```bash
ledger token create --role ingest --unscoped   # accepts spans for ANY tenant
```

```python
ledger = HTTPExporter("https://ledger.../spans", auth_token=os.environ["AGENT_CAPTURE_LEDGER_TOKEN"])
# per request/tenant, set the compliance default's end_customer_id to that tenant.
```

An unscoped token is a **trusted first-party credential** (it can attribute
spans to any tenant) — see `docs/key-management.md`.

**Per-tenant tokens instead?** If you'd rather give each tenant its own ingest
token (independent revocation / isolation), use the `TenantRoutingExporter`,
which dispatches each span by its `end_customer_id`:

```python
from agent_capture.exporter import TenantRoutingExporter

exporter = TenantRoutingExporter.from_tokens(
    "https://ledger.../spans",
    {"acme-bank": acme_token, "demo-co": demo_token},
)
# or, for onboarding-time refresh:
# TenantRoutingExporter.from_token_provider(endpoint, lambda tenant: lookup_token(tenant))
```

It builds a per-tenant pipeline lazily, drops + logs (`AC414`) spans for a tenant
with no token, and never raises into your agent.

### Serverless (Cloud Run / Lambda) — `flush_on_root_close`

The default exporter ships spans on a **background thread**. On serverless
runtimes that **throttle CPU after the response returns** (Cloud Run with
`cpu_idle=true`, AWS Lambda), that thread is starved and **queued spans never
ship — silently**. For a compliance tool that's the worst failure mode: capture
looks fine, but nothing lands.

Turn on **`flush_on_root_close`** — the SDK then flushes the exporter
**synchronously when each trajectory's root span closes**, so the whole
trajectory is shipped before your handler returns:

```python
configure(
    exporter=BoundedQueueExporter(HTTPExporter("https://ledger.../spans", auth_token=token)),
    default_compliance=default_compliance,
    flush_on_root_close=True,   # serverless: ship synchronously at end of trajectory
)
```

This adds the ledger round-trip to request latency — correct for serverless,
unnecessary for a long-running process (leave it `False` there; the background
drain is cheaper). If `flush()` ever fails it's logged (`AC416`) and never
raises into your handler.

---

## 7. Cross-process trajectories — W3C Trace Context (1 minute)

When agent A invokes agent B over HTTP, inject the trace context on the
outbound side:

```python
from agent_capture.context import inject
import httpx

with httpx.Client() as client:
    response = client.post(
        sub_agent_url,
        json=payload,
        headers={"Content-Type": "application/json", **inject()},
    )
```

And extract on the inbound side:

```python
from agent_capture.context import extract

@app.post("/agent-b/run")
def run(request: Request) -> Response:
    remote = extract(dict(request.headers))
    if remote is not None:
        # B's root span will use remote.trajectory_id as its trace id
        # and remote.span_id as its parent. (Wire-up support for this
        # path lives in builder.open(parent=...) in a future release.)
        ...
```

The headers used (`traceparent`, `tracestate`) are the W3C Trace Context
standard, so any OTel-aware downstream system sees a coherent trace.

---

## 8. Operations — observability and error codes

Every internal failure logs a one-line diagnostic on the standard
`agent_capture` logger with a stable error code:

```
[AC401] FileExporter.export failed: [Errno 28] No space left on device — fix: check disk space, …
[AC404] HTTPExporter: dropping 100 spans after retries: ConnectError — fix: the ledger is unreachable or …
[AC406] Dropped CRITICAL span (side_effect) after 1.00s block — fix: increase max_size on the queue …
```

Attach a handler to the `agent_capture` logger to route those into your
ops stack:

```python
import logging
handler = logging.FileHandler("/var/log/agent-capture.log")
handler.setLevel(logging.WARNING)
logging.getLogger("agent_capture").addHandler(handler)
```

The full code reference is in `agent_capture._internal.safelog.ErrorCode`
(or grep the source for `ACxxx`).

---

## Common pitfalls

- **No spans appear.** Check that `configure()` was called *before* the
  first `@traced` function ran. The decorator falls back to a no-op
  pass-through with an `[AC102]` debug log if no builder is registered.

- **`@traced` raises ImportError on the LangGraph adapter import.**
  Install the extra: `pip install 'agent-capture[langgraph]'`.

- **`HmacFingerprint: env var ... is unset or empty`.** The customer's
  HMAC key must be present in the env var named in the policy. Pass
  `key=b"..."` only in tests.

- **`content_hash` differs between two runs of the same logical agent.**
  Inputs like timestamps and UUIDs change between runs. The chain only
  requires that *within a single trajectory*, each child's
  `parent_content_hash` matches its parent's `content_hash` — which it
  will, deterministically.

- **Spans are dropped under load.** The `BoundedQueueExporter` drops
  oldest non-critical spans when the inner exporter can't keep up. Check
  `pipeline.dropped_count` and tune `max_size` or `batch_max_wait_s`.

---

## What's next

The recorder is one of six layers in the full compliance stack. The
downstream layers (redaction policy management, tamper-evident ledger,
reporting, enforcement, query) live in separate packages and consume the
trajectories this SDK produces. See `docs/architecture.md` for the full
picture.
