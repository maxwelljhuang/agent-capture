# Trajectory Capture Engine — High-Level Architecture

> The recorder layer for AI agent compliance infrastructure.
>
> The source PDF lives at `/Users/maxwellhuang/Documents/recorder_architecture.md.pdf`.
> This file is the markdown projection of that document and the load-bearing
> spec for the code in this repo. Section numbers below match the PDF.
>
> **Revised 2026-06-02 — trust topology.** The original PDF assumed an
> in-tenant deployment (ledger + downstream in the bank's environment). The
> market is SaaS: the agent vendor processes the bank's data and stores it in
> the **vendor's cloud**, so the ledger and all downstream layers run there by
> default (in-tenant remains a supported enterprise option). This `.md` reflects
> the current model and is authoritative over the PDF wherever they disagree on
> topology; see `CLAUDE.md` for the canonical statement.

## 1. What this document is

The architecture for the **capture engine** — the first of the six layers in
the compliance stack (recorder → redaction → ledger → reporting → enforcement
→ query). The SDK that agent vendors install. Every architectural decision
downstream depends on this layer producing the right data, in the right shape,
at the right point in the agent's execution.

The capture engine is the only component that runs inside the agent's hot
path. Everything else operates on the output it produces. If the capture
engine is wrong — wrong schema, wrong placement, wrong performance profile —
nothing downstream can recover.

## 2. Design principles

1. **The trajectory is the unit, not the model call.** A trajectory is the
   complete directed graph of steps an agent took to produce a legally
   consequential outcome.
2. **Capture must never break the host.** Capture failures must be invisible
   to the agent's primary execution path.
3. **Capture must be cheap to integrate.** Under one hour for the vendor's
   developer.
4. **The schema is forever.** Once vendors integrate, changing it requires
   every vendor to re-instrument.
5. **Build on standards, extend where they fall short.** OpenTelemetry, GenAI
   semantic conventions, LangSmith and Braintrust patterns are the baseline.

## 3. System overview

Four internal components:

- **Instrumentation layer** — integration surface (framework adapters, SDK
  wrappers, manual decorators).
- **Span builder** — constructs typed spans, enforces schema, maintains
  parent-child relationships across async.
- **Redaction filter** — in-process, before any span leaves the agent.
- **Exporter** — async batched shipping to file / HTTP / OTel destinations.

Flow: agent event → instrumentation fires → span builder constructs span →
redaction sanitizes in place → exporter queues for async shipment.

## 4. The span schema

### 4.1 Core fields (OpenTelemetry-compatible)
`span_id`, `parent_span_id`, `trajectory_id`, `name`, `type`, `start_time`,
`end_time`, `status`, `attributes`, `inputs`, `outputs`, `error`.

### 4.2 Span types
`model_call`, `tool_call`, `retrieval`, `planner_step`,
`sub_agent_invocation`, `human_approval`, `side_effect`, `policy_check`.

### 4.3 Compliance metadata (every span)
`policy_version_active`, `prompt_template_version`, `model_card_version`,
`tool_schema_version`, `agent_version`, `end_customer_id`, `subject_id`,
`regulatory_regime`, `retention_class`, `data_classification`.

### 4.4 Provenance fields
`content_hash`, `parent_content_hash`, `schema_version`.

## 5. Instrumentation layer
Three tiers: framework adapters (LangGraph first), SDK wrappers
(`wrap(Anthropic())`), manual decorator (`@traced`). MCP interceptor deferred
past v1 but schema must accommodate it.

## 6. Context propagation
Python `contextvars.ContextVar`; Node `AsyncLocalStorage`; Go
`context.Context`. Use OpenTelemetry's Context API where available.

## 7. Cross-process and sub-agent linking
W3C Trace Context inject/extract on HTTP / queue / function payloads.

## 8. Redaction filter
In-process, synchronous, before queueing. Schema-aware (typed sensitive
fields) + pattern-based (Presidio + finance pack). Strategies: full redaction
or HMAC fingerprint with a customer-managed key (BYOK via KMS), injected into
the vendor-side recorder at use time. The guarantee is about what we durably
persist — the downstream ledger never holds raw PII. Policy is bank-authored
and vendor-side enforced, over a non-negotiable minimum redaction floor.

## 9. Exporter
Async batched queue, bounded, drops oldest non-critical on backpressure.
Failure handling: backoff, persist-on-shutdown, replay-on-startup, never
throw. Pluggable destinations: file (JSONL), HTTP (vendor-cloud ledger by
default; in-tenant optional), OTel (OTLP).

## 10. Performance budget
Per-span overhead < 1ms p99. Memory < 100MB steady-state. CPU < 2% sustained.
No measurable impact on agent p99 latency.

## 11. Out of scope (downstream layers)
Storage, query, real-time enforcement, reporting.

## 12. Build sequence
- Week 1 — schema finalized + scenarios
- Week 2 — Python OTel-based lib, file exporter, manual decorator
- Week 3 — LangGraph adapter + Anthropic/OpenAI SDK wrappers
- Week 4 — redaction filter + HTTP exporter
- Week 5 — polish, perf validation, docs
- Week 6 — external integration test with loan-denial toy agent

## 14. Ledger (layer 2)
The vendor-cloud ledger receives spans from the recorder's `HTTPExporter`,
verifies the canonical hash chain on write, stores spans append-only in
partitioned Postgres, and emits periodic Ed25519-signed Merkle attestations
over closed trajectories.

Build details — package: `packages/ledger/`. Stack: FastAPI + asyncpg +
SQLAlchemy 2.x + Alembic. The trust posture is the central feature: the
ledger runs in the **vendor's cloud** by default (in-tenant deployment is a
supported enterprise option), and trust rests on cryptographic provenance plus
pre-export redaction — the durable record never holds raw PII — rather than on
where the bytes physically sit. Append-only is enforced by a `BEFORE UPDATE OR
DELETE` trigger gated to the `ledger_retention` role — application code
runs as `ledger_app` and cannot mutate spans by construction, only by
escalating to a separate process with separate credentials.

Tamper evidence rests on two layers. (1) Per-span: the canonical
`content_hash` from the recorder is recomputed at ingest; mismatches are
quarantined. The `parent_content_hash` link is verified for any span whose
parent is already stored. (2) Per-window: every
`LEDGER_ATTESTATION_INTERVAL_SECONDS`, the anchor worker computes one
Merkle root per `end_customer_id` over the trajectory-root hashes of
trajectories closed in the window, signs it with the Ed25519 key from
`LEDGER_SIGNING_KEY_PATH`, and exports the signed envelope to a configured
external sink (file/S3/webhook). A regulator can recompute a trajectory's
root from the stored `body`, walk a Merkle inclusion proof from
`GET /attestations/proof/{trajectory_id}`, and compare against the signed
archive — detection of tampering does not require trusting the ledger.

Retention is `RetentionClass` × age, with default windows of 7d
(`TRANSIENT`), 90d (`STANDARD`), 7y (`EXTENDED`), and indefinite under
`LITIGATION_HOLD`. The worker prefers `DROP TABLE` of whole monthly
partitions; mixed partitions fall back to row-level `DELETE`. Every
operation writes a `retention_operations` audit row.

Reads are role-scoped (`reader` tokens see only their own
`end_customer_id`); `admin` is unscoped but every read writes an
`access_log` row — including reads of the access log itself.

See `packages/ledger/README.md` for the operator's quick start and the
plan at `.claude/plans/foamy-napping-bee.md` for the full design.

## 13. Parallels summary
- **Copy directly** from LangSmith/Braintrust: span data model, framework
  adapter pattern, SDK wrappers, manual decorator, context propagation,
  W3C linking, async batching exporter, OTel compatibility.
- **Extend**: span type taxonomy (add `human_approval`, `side_effect`,
  `policy_check`), compliance metadata attributes, GenAI semantic conventions.
- **Build differently**: in-process pre-export redaction with bank-authored,
  vendor-enforced policy; cryptographic provenance fields; default destination
  is the vendor-cloud ledger (in-tenant optional).

The capture mechanics are settled engineering. Differentiation is in schema
choices, redaction posture, and provenance hooks. Spend the design effort
there; copy everything else.
