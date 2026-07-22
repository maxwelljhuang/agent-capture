# SDK enhancement suggestions (from a production integration)

These suggestions come from integrating `agent-capture` into a real
multi-tenant agent product (**ai-employee** — Python · FastAPI · LangGraph ·
LiteLLM, deployed on Cloud Run, serving HOA tenants over typed chat **and**
Gemini Live voice). Each item records the concrete friction or gap that
surfaced, the workaround used in the consumer, and the proposed SDK change,
with a link to the tracked issue.

Two of these were already implemented during the integration and merged:
- **GCP KMS Ed25519 signer** — PR #61 (see §8)
- **Exporter `flush()`** — PR #62 (see §2)

Priority legend: 🔴 high · 🟡 medium · 🟢 low.

| # | Area | Issue | Priority |
|---|---|---|---|
| 1 | Multi-tenancy | [#63](https://github.com/maxwelljhuang/agent-capture/issues/63) | 🔴 |
| 2 | Serverless shipping | [#64](https://github.com/maxwelljhuang/agent-capture/issues/64) | 🔴 |
| 3 | Instrumentation seam | [#65](https://github.com/maxwelljhuang/agent-capture/issues/65) | 🔴 |
| 4 | Span lifecycle (streaming) | [#66](https://github.com/maxwelljhuang/agent-capture/issues/66) | 🟡 |
| 5 | Trajectory linking | [#67](https://github.com/maxwelljhuang/agent-capture/issues/67) | 🟡 |
| 6 | Redaction — free-text PII | [#68](https://github.com/maxwelljhuang/agent-capture/issues/68) | 🟡 |
| 7 | Redaction — BYOK keys | [#69](https://github.com/maxwelljhuang/agent-capture/issues/69) | 🟡 |
| 8 | Signing — KMS | [#70](https://github.com/maxwelljhuang/agent-capture/issues/70) | 🟡 |
| 9 | Tooling / distribution | [#71](https://github.com/maxwelljhuang/agent-capture/issues/71) | 🟡 |
| 10 | Ops / observability | [#72](https://github.com/maxwelljhuang/agent-capture/issues/72) | 🟢 |
| 11 | Schema extensibility | [#74](https://github.com/maxwelljhuang/agent-capture/issues/74) | 🟢 |

---

## 1. Built-in tenant-routing exporter + unscoped ingest token — [#63](https://github.com/maxwelljhuang/agent-capture/issues/63) 🔴

**The single biggest integration blocker.** A multi-tenant process can't ship
to the ledger out of the box: each ingest token is pinned to one
`end_customer_id`, and `require_role("ingest")` plus the CLI's mandatory
`--customer` mean one token serves one tenant. The consumer runs **one process
for many tenants**, stamping `end_customer_id = tenant_id` per span.

**Workaround in the consumer.** A `TenantRoutingExporter` implementing the
`SpanExporter` protocol: holds `{tenant_id: HTTPExporter(token)}`, dispatches
each span by `span.compliance.end_customer_id`, lazily builds per-tenant
exporters, and drops+logs spans for tenants with no token. ~70 lines of generic
plumbing every multi-tenant SaaS recorder will re-invent.

**Proposed.**
1. Ship a tenant-routing exporter in the SDK (config: a `{tenant: token}` map or
   a `token_provider(tenant)` callable, for onboarding-time refresh).
2. Support an **unscoped ingest token.** The ingest tenant check already no-ops
   when the token's `end_customer_id` is null — only the CLI blocks minting one.
   A trusted first-party unscoped token removes per-tenant token churn entirely.

---

## 2. Flush-on-root-close serverless mode + Cloud Run/Lambda docs — [#64](https://github.com/maxwelljhuang/agent-capture/issues/64) 🔴

**Problem.** The async background-drain model assumes a long-running process
whose thread keeps getting CPU. On Cloud Run with `cpu_idle=true` (CPU only
during requests), the drain thread is **starved after the response returns**, so
queued spans never ship — **silently**. This cost a full debug cycle: capture
initialized, turns ran, but zero trajectories landed.

**Workaround.** First `cpu_idle=false` (ongoing CPU cost), then a `flush()`
added to `BoundedQueueExporter` + `HTTPExporter` (**PR #62**, merged) plus a
per-request `flush_capture()` at turn/voice-session end in the consumer, which
let `cpu_idle=true` return with no span loss.

**Proposed.**
- First-class serverless mode: a **flush-on-root-span-close** option (ship the
  trajectory synchronously when the root closes), or a `serverless=True`
  exporter.
- Document the Cloud Run / Lambda pattern — the default config silently drops
  spans there, which is a sharp edge for a compliance tool.

---

## 3. LiteLLM adapter + "instrument at the LLM client" guidance — [#65](https://github.com/maxwelljhuang/agent-capture/issues/65) 🔴

**Problem.** Guidance nudged toward instrumenting the agent class. But the
orchestrator called the LLM router **directly** (intent detection, routing,
RAG), bypassing the agent class → **zero `model_call` spans** for a normal chat
turn. The real choke point is the LLM client. Also, the app uses **LiteLLM**,
which no existing adapter/wrapper covers (only Anthropic/OpenAI/LangGraph/CrewAI).

**Workaround.** Moved `model_call` capture to the LLM **router** (`generate` /
`generate_stream`) + a per-turn root span in the orchestrator entry point.

**Proposed.**
- Add a **LiteLLM adapter** (`wrap(litellm)` / callback handler).
- Add a generic "wrap your LLM client function" helper for custom orchestrators.
- Update guidance: **instrument at the LLM client, not the agent class** — the
  agent class is often bypassed by direct client calls.

---

## 4. Manual span handle for long-lived / realtime-streaming spans — [#66](https://github.com/maxwelljhuang/agent-capture/issues/66) 🟡

**Problem.** `traced` is a context manager — ideal for scoped spans, but it
can't cleanly model a span that **opens on event N and closes on event M** across
async iterations. Capturing **Gemini Live** (bidirectional realtime audio)
turns, there was no way to open a `model_call` at turn-start and close it at
`turnComplete`, so spans were emitted at turn-end (content complete, timing
≈point-in-time).

**Proposed.**
- A low-level span handle: `span = builder.start(...)` → `span.end(...)`,
  preserving the never-raise / no-op guarantees.
- A documented realtime/streaming-session pattern (open root → accumulate turns
  → close). Bidirectional voice (Gemini Live, OpenAI Realtime) is a growing
  class the request/response model doesn't fit.

---

## 5. Cross-entry-point trajectory continuation API — [#67](https://github.com/maxwelljhuang/agent-capture/issues/67) 🟡

**Problem.** Separate entry points in the same logical session each open their
**own root trajectory**: the voice session loop and the workflow `resume`
(human_approval) are disconnected from the originating run. W3C trace context
covers cross-process, but there's no clean way to continue an existing
trajectory from a separate **in-process** entry point.

**Proposed.** A `continue_trajectory(trajectory_id, parent_span_id=...)` API
(or expose `builder.open(parent=...)` through `traced`) so related entry points
nest into one trajectory.

---

## 6. Lightweight free-text name/address recognizers — [#68](https://github.com/maxwelljhuang/agent-capture/issues/68) 🟡

**Problem.** The built-in regex finance pack is excellent — it redacts
SSN/account/routing/DOB in free text with **no presidio/spaCy dependency**
(validated: a spoken SSN in a voice transcript → `[REDACTED:ssn]`). But it only
catches structured fields and pattern types. **Free-text names and addresses** —
which homeowners type and speak constantly — are not redacted, and the full
presidio extra is too heavy for many deployments.

**Proposed.** A lightweight name/address recognizer (or a pluggable recognizer
pack) that doesn't pull the full presidio/spaCy weight.

---

## 7. Per-tenant BYOK HMAC keys — [#69](https://github.com/maxwelljhuang/agent-capture/issues/69) 🟡

**Problem.** `HmacFingerprint` reads a single fixed `key_env`, so per-tenant
**customer-owned (BYOK)** keys aren't possible. Each bank/tenant should own and
rotate its own fingerprint key — this blocks regulated multi-tenant onboarding.

**Proposed.** Per-span HMAC key resolution by `end_customer_id` (e.g. a
`key_provider(tenant)` hook), so multi-tenant BYOK works without standing up a
separate redaction filter per tenant. (Note: rotating a tenant's key
invalidates its existing `[FP:…]` correlations — document accordingly.)

---

## 8. Generalize cloud KMS signers + recommend over file keys — [#70](https://github.com/maxwelljhuang/agent-capture/issues/70) 🟡

**Context.** A file signing key generated outside KMS is a weak tamper-evidence
root (its private material can transit untrusted environments). **PR #61** added
a `KmsEd25519Signer` (GCP Cloud KMS, Ed25519/PureEdDSA) implementing the `Signer`
protocol and returning the same raw Ed25519 public key, so verification is
byte-compatible. (Cloud KMS supports `ec-sign-ed25519`, confirmed.)

**Proposed.**
- Generalize: ship AWS KMS / Azure Key Vault signers (the `Signer` protocol
  already makes this a drop-in).
- Recommend KMS over file keys in docs — a file signing key is a footgun for the
  product's core integrity guarantee.

---

## 9. CLI robustness + programmatic API + PyPI distribution — [#71](https://github.com/maxwelljhuang/agent-capture/issues/71) 🟡

**Problem.** The `ledger`/`token` Typer CLI **broke** in the integration
environment on `str | None` option annotations (Typer/Click version
sensitivity). Migrations and token-minting had to be done by **bypassing the
CLI** — running Alembic `upgrade head` directly and calling `TokenRepo.create`
reverse-engineered from the CLI source. Separately, the SDK isn't on a package
index, which forced wheel-vendoring in the consumer image.

**Proposed.**
- Pin/relax Typer and add a CLI smoke test across common versions.
- Expose token-minting + migrations as a stable **programmatic Python API** so
  consumers aren't forced through the CLI.
- Publish to PyPI (or document a private-index/Artifact-Registry story).

---

## 10. Recorder self-observability metrics — [#72](https://github.com/maxwelljhuang/agent-capture/issues/72) 🟢

**Problem.** The `[ACxxx]` safelog is useful, but ops needs metrics: queue
`dropped_count`, flush latency, per-tenant routing drops, ingest failures. The
consumer had to track `dropped_unknown` itself in the routing exporter.

**Proposed.** A built-in metrics hook (Prometheus/OTel) for the recorder +
exporters: drop counters, flush latency, per-destination success/failure.

---

## 11. Extensible regulatory_regime + provider token-usage normalization — [#74](https://github.com/maxwelljhuang/agent-capture/issues/74) 🟢

**Problem.** `regulatory_regime` is a finance-specific frozen enum (no FERPA, no
HOA/education); non-lending domains don't fit cleanly. Separately, provider
token-usage (e.g. Gemini Live `usageMetadata`) isn't normalized into the
`model_call` token fields.

**Proposed.**
- A `custom_regime` string escape hatch alongside the enum (preserves
  "schema is forever" while broadening applicability).
- Adapters normalize provider token-usage into the existing
  `input_tokens`/`output_tokens`/`total_tokens` fields.

---

## Recommended sequencing

1. **#63** multi-tenant ingest — removes the top integration blocker.
2. **#64** serverless flush mode — prevents silent span loss on Cloud Run/Lambda.
3. **#65** LiteLLM adapter + client-level guidance — prevents the wrong-layer
   instrumentation mistake.
4. **#69** BYOK + **#68** free-text PII — needed before regulated multi-tenant
   onboarding.
5. Everything else as capacity allows.

*Source integration write-up: `ai-employee/docs/agent-capture-implementation-summary.md`.*
