# Repository conventions for Claude sessions

## What this is
`agent-capture` is the **compliance infrastructure stack for AI agents** that
make legally consequential decisions (loan underwriting, KYC, fraud review).
The Python + TypeScript SDK captures an agent's full decision **trajectory**;
downstream layers store it tamper-evidently and turn it into regulatory
artifacts. `docs/architecture.md` (and its source `recorder_architecture.md.pdf`)
is the load-bearing design doc — everything in this repo serves that design.

The conceptual pipeline has six stages: **recorder → redaction → ledger →
reporting → enforcement → query**. Redaction ships as an in-process *component
of the recorder* (architecture §3). **This repo is scoped to the vendor-cloud
layers only:** recorder (L1, incl. redaction) → ledger (L2) → reporting (L3) →
enforcement (L5) — the SDK the vendor installs in their agent, plus the services
that run in the **vendor's cloud** over the vendor's trajectory data. The
**query + dashboard layer ("Kelp") is a SEPARATE repository** in **our** cloud
(see "Dashboard / query layer" below); do not build it here. Read
`docs/reporting-fields.md` for the contract mapping regulator questions to span
fields, and `docs/integration-guide.md` for the vendor's install path.

### Trust topology (the organizing principle)
The **recorder runs inside the agent *vendor's* product**, and the agent vendors
we sell to run as **SaaS**: the bank sends its data to the vendor, and the vendor
processes and stores it in the **vendor's cloud**. So everything downstream —
ledger, reporting, enforcement, query — runs in the **vendor's cloud** too,
alongside the agent. (In-tenant deployment into the bank's own environment
remains a supported enterprise option, just not the default; the exporter stays
destination-pluggable.) This split drives the hard rules below — redaction is
in-process so the durable compliance record never holds raw PII, provenance is
cryptographic, and the default export destination is the **vendor-cloud
ledger**. The recorder is the *only* component in the agent's hot path; if it
produces the wrong data, nothing downstream can recover.

### Dashboard / query layer — a SEPARATE repo, not this one
The observability/traceability dashboard ("Kelp") is a **separate repository, in
our cloud, under our brand** — React frontend, officer SSO, and a
backend-for-frontend. It is the **control plane**; regulated trace data stays in
the **vendor's cloud** (the **data plane**). Kelp's backend **queries this repo's
ledger query API over the wire** and never durably persists regulated data in
our cloud (it stores only control-plane data: accounts, permissions, dashboard
config, saved queries, metadata/pointers, access audit logs). **Do not build
dashboard / BFF / frontend / federated-query code in this repo.** The only seam
this repo owns is the **ledger's query API** (`GET /trajectories`,
`/trajectories/{id}`, `/trajectories/{id}/spans`, `/stats`, `POST /verify/{id}`,
`/attestations*`) — harden *that* for the dashboard to consume; the dashboard
itself lives elsewhere.

## Design principles (from architecture §2 — let these settle design debates)
1. **The trajectory is the unit, not the model call.** A trajectory is the whole
   directed graph of steps behind one outcome. Capturing an isolated call is
   useless for compliance.
2. **Capture must never break the host** — the cardinal rule: *the agent must
   always win*. If the choice is dropping a span or crashing the host, drop the
   span.
3. **Capture must be cheap to integrate** — vendor developer integrates in under
   an hour; no architectural changes to their agent.
4. **The schema is forever.** Changing it forces every vendor to re-instrument.
   Lock it carefully with downstream consumers in mind (this is why Week 1
   freezes the schema before anything depends on it).
5. **Build on standards, extend where they fall short.** Copy capture mechanics
   from OpenTelemetry / LangSmith / Braintrust; the differentiation is only in
   the compliance span types, the redaction posture, and the provenance hooks.

## Source of truth
- **Pydantic models** in `packages/python/src/agent_capture/schema/` are the
  canonical span schema. They generate `schemas/span.schema.json`, which in
  turn generates `packages/typescript/src/schema/*.ts`.
- Never edit `schemas/span.schema.json` or generated TS files by hand.
- After changing a Pydantic model, regenerate:
  `uv run python scripts/generate_schema.py && ./scripts/generate_ts_types.sh`

### Span schema at a glance (architecture §4)
Every span carries three field groups:
- **Core (OTel-compatible):** `span_id`, `parent_span_id`, `trajectory_id`,
  `name`, `type`, `start_time`, `end_time`, `status`, `inputs`, `outputs`,
  `error`.
- **Compliance metadata (captured at creation, never reconstructed):**
  `policy_version_active`, `prompt_template_version`, `model_card_version`,
  `tool_schema_version`, `agent_version`, `end_customer_id`, `subject_id`,
  `regulatory_regime`, `retention_class`, `data_classification`.
- **Provenance (for the ledger's hash chain):** `content_hash`,
  `parent_content_hash`, `schema_version`.

Span `type` is one of: `model_call`, `tool_call`, `retrieval`, `planner_step`,
`sub_agent_invocation`, `human_approval`, `side_effect`, `policy_check`. The
last three are the compliance-specific additions over generic observability —
downstream reporting keys off them (e.g. "did the agent actually send the denial
letter?" is a `side_effect`, not a `tool_call`).

## Non-negotiable rules
1. **Capture never raises into the host agent.** Every public entry point in
   the SDK must be wrapped so an internal failure logs locally and returns the
   host's original return path untouched. No exception ever crosses the SDK
   boundary into vendor code. (Downstream layers — ledger, reporter — run
   off the hot path and *may* raise.)
2. **No dropping `human_approval` or `side_effect` spans.** The bounded queue
   may drop other span types under backpressure; these two are critical and
   must survive.
3. **Redaction runs in-process, before any span leaves the agent's memory** —
   so the durable compliance record (the ledger) never holds raw PII. (In SaaS
   the bank's data is already in the vendor's cloud because the agent processes
   it; the guarantee is about what we *persist*, not about keeping data inside
   the bank.) The redaction policy is **bank-authored, vendor-side enforced**, on
   top of a non-negotiable minimum redaction floor we guarantee — the vendor
   never weakens it. Strategies: full redaction (`[REDACTED:type]`) or HMAC
   fingerprint with a **customer-managed key (BYOK via KMS)**, since the bank may
   have no environment in the data path.
4. **`content_hash` is computed over the canonical serialization** defined in
   `schema/canonical.py`. Python and TypeScript must produce byte-identical
   bytes for a given logical span; golden fixtures in `tests/scenarios/` are
   the contract.
5. **Exporter never blocks the agent.** Async batched queue, bounded; on
   backpressure drop oldest non-critical spans (with a counter), persist on
   graceful shutdown, replay on restart, retry with backoff — never propagate.

## Performance budget (architecture §10 — `tests/perf/` enforces these)
Per-span overhead < 1ms p99 · memory < 100MB steady-state · CPU < 2% sustained
· no measurable impact on the agent's p99 latency.

## Layout
| Path | Role | Layer |
| --- | --- | --- |
| `packages/python/src/agent_capture/schema/` | Pydantic source of truth | recorder (L1) |
| `packages/python/src/agent_capture/span/` | Span builder + lifecycle | recorder (L1) |
| `packages/python/src/agent_capture/context/` | contextvars + W3C TraceContext (cross-process / sub-agent linking) | recorder (L1) |
| `packages/python/src/agent_capture/instrumentation/` | Framework adapters (LangGraph, CrewAI), SDK wrappers, `@traced` decorator | recorder (L1) |
| `packages/python/src/agent_capture/redaction/` | In-process, pre-export redaction pipeline | recorder (L1) component |
| `packages/python/src/agent_capture/exporter/` | Bounded queue, batcher, file/HTTP/OTel destinations | recorder (L1) |
| `packages/typescript/src/` | Mirror structure for the TS SDK (parity with Python) | recorder (L1) |
| `packages/ledger/` | Vendor-cloud ledger (in-tenant optional): append-only Postgres store, ingest hash-chain verification, Ed25519 Merkle attestations, retention, role-scoped reads | ledger (L2) |
| `packages/reporter/` | Notice renderers: `ecoa/` (Adverse Action, per-trajectory) + `sr_11_7/` (Model Inventory, corpus+registry); shared `common/`; PDF/HTML + audit manifest | reporting (L3) |
| _(not built)_ | **Enforcement** — evaluates rules on the live span stream and takes blocking action | downstream |
| _(not built)_ | **Query** — search / multi-trajectory aggregates read from the ledger | downstream |

Each downstream package is its own uv workspace member depending on
`agent-capture`; they consume the schema, never alter it.

## Testing
- `tests/unit/` — module-level tests, no network, no I/O outside tmp dirs
- `tests/scenarios/` — schema validated against realistic compliance
  trajectories; golden fixtures are the canonical-serialization contract. The
  three reference scenarios are loan approval/denial, KYC check, and fraud flag.
- `tests/integration/` — multi-component flows (LangGraph + SDK wrap, etc.)
- `tests/perf/` — pytest-benchmark suites enforcing the §10 budgets above
- Repo-wide standard: `ruff check`, `ruff format`, and `mypy --strict` clean.

## Build sequence
Six-week plan in `docs/architecture.md` §12 (schema → Python lib + file exporter
→ adapters + SDK wrappers → redaction + HTTP exporter → polish/perf → vendor
integration test on the loan-denial flow). Don't skip ahead; the schema must be
locked in Week 1 before downstream layers depend on it. Downstream layers
(ledger, reporting) are built as separate packages once the recorder schema is
stable.
