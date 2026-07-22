# Enforcement Layer (L5) — Implementation Plan

## Executive summary

Enforcement is the first layer that *acts* rather than observes. It evaluates
bank-authored compliance rules against the agent's trajectory and can **gate the
two span types that touch the irreversible outside world** — `side_effect` and
`human_approval` — while treating every other span type as **advisory only**
(emit a `policy_check` span + alert, never block). This directly collides with
the recorder's cardinal rule (*"capture must never raise into / block / crash
the host — the agent always wins"*); the entire design is about scoping **where**
we are allowed to violate that rule (only the two gated types, only for
opted-in rules) and **how we fail** when the rule engine can't decide.

Three confirmed design decisions anchor the build:
1. **Selective gating** of `side_effect` + `human_approval` (already the
   recorder's "critical/undroppable" set, `exporter/queue.py:31-33`); all else
   advisory.
2. **Tiered failure mode, per rule**: reversible → *fail-open* (advisory);
   irreversible → default *fail-to-human* (hold + escalate, reusing the
   `human_approval` span); *fail-closed* (hard block) only as bank-chosen,
   vendor-consented, per-rule opt-in — never the global default.
3. **Inline blocking, centralized brain**: the recorder-side gate calls a
   **vendor-cloud verdict service synchronously** (~150ms budget) for gated
   spans, backed by a tiny **local failure-mode fallback table** (failure-mode
   *metadata only*, never rule logic) so a `fail_closed` rule still holds during
   a cloud outage.

**Zero schema change in v1.** Advisory verdicts reuse `PolicyCheckAttributes`;
fail-to-human reuses `HumanApprovalAttributes` (incl. its existing
`decision="escalated"`); verdict→gated-span linkage rides the existing
`parent_content_hash` chain; rule version rides
`ComplianceMetadata.policy_version_active`. The frozen v1.0.0 schema
(`SCHEMA_VERSION`, `schema/canonical.py`) is untouched.

**New code lives in two places, with a one-way dependency**: a thin **recorder
gate hook** (`packages/python/.../enforcement/`, no-op by default) that publishes
a Protocol contract, and a separate **`packages/enforcement/` engine** (vendor
cloud) that implements it. The recorder never imports the engine — separation of
concerns is preserved by the Protocol boundary + a registry (mirroring the
existing `_suppress_model_call` contextvar pattern).

### Assumptions (flagged)
- A1. The agent process runs inside the vendor's product; "inline" gate code and
  the cloud verdict service are both vendor-controlled (consistent with the
  corrected SaaS trust topology). Banks author rules; the vendor enforces them.
- A2. Gated steps (`side_effect`/`human_approval`) are rare (≈≤1 per trajectory)
  and latency-tolerant — the premise that makes a synchronous cloud call and an
  inline human-hold acceptable.
- A3. v1 rules evaluate the **immediate span only**; trajectory-ancestor-aware
  rules ("block payment unless a prior human_approval exists") are v2.
- A4. The reviewer **UI** is out of scope for L5 and belongs to the L6
  query/dashboard layer; L5 ships the hold queue + REST resolve API.

---

## 1. Component architecture & where each piece runs

```
            VENDOR PRODUCT (agent hot path)        │        VENDOR CLOUD
 ───────────────────────────────────────────────  │  ────────────────────────────
  agent → @traced(side_effect/human_approval)      │
        │                                          │
        ▼  pre-func seam (decorator.py)            │
  [ recorder gate hook ]  enforcement/gate.py      │
   - EnforcementGate Protocol (published contract) │
   - Verdict / GateRequest value types             │
   - registry: set_gate()/current_gate() (no-op    │
     default → today's behavior, zero latency)     │
   - "never crash host" try/except wrapper         │
        │  evaluate(GateRequest)                    │
        │   ───────── sync HTTP (~150ms) ─────────▶ │  [ verdict service ]  packages/enforcement
        │                                          │   - rule loader (mirror redaction/policy.py)
        │   ◀──── allow | hold | block ──────────  │   - evaluator (immediate-span rules)
        ▼  (on unreachable/timeout)                 │   - per-tenant scoping (reuse auth.py)
  [ local failure-mode fallback table ]            │   - emits/persists verdict + audit
   - (end_customer_id, action_class) → mode        │   [ hold queue + review API ]
   - metadata only; NO rule logic                  │   [ timeout worker ] (mirror retention_job)
                                                    │  [ advisory evaluator ] hooked into
                                                    │   ledger ingest (_validate_one)
```

**A. Recorder gate hook** — new package dir `packages/python/src/agent_capture/enforcement/`:
- `gate.py`: `EnforcementGate` Protocol (single `evaluate`/`evaluate_async`),
  `Verdict` + `GateRequest` frozen dataclasses, the process-wide registry
  (`set_gate`/`current_gate`, default `None`) — mirror `_internal/runtime.py`
  `set_default_builder`/`default_builder` and the `_suppress_model_call`
  contextvar in `context/propagation.py:35`.
- `_apply_gate(open_span) -> GateOutcome`: the call-site glue invoked from
  `decorator.py`. Fast no-op unless `current_gate() is not None` **and**
  `open_span.type in _GATED_TYPES` (reuse `exporter/queue.py` `_CRITICAL_TYPES`,
  re-exported as `_GATED_TYPES` — identical set by design).
- Owns the "never crash the host" discipline (try/except mirroring
  `instrumentation/sdk_wrappers/_common.py`): any *internal* gate failure →
  `log_error(ErrorCode.AC5xx)` → treat as the rule's unreachable failure-mode.
  The only exception that ever reaches the host is the deliberate
  `EnforcementBlocked` (block/reject).
- **No imports from `packages/enforcement`.** Dependency arrow is one-way:
  engine → recorder contract.

**B. Enforcement engine** — new uv workspace member `packages/enforcement/`
(mirrors `packages/ledger` layout): rule model + loader, evaluator, the inline
**verdict service** API, the **hold queue** tables + review/resolve API, the
**timeout worker**, and the **advisory evaluator** that hooks the ledger ingest
path. Depends on `agent-capture` (schema) and reuses ledger auth/audit patterns;
it provides an `EnforcementClient` that satisfies the recorder's
`EnforcementGate` Protocol.

**Wiring (preserves SoC):** the vendor's app startup registers the client:
`agent_capture.enforcement.set_gate(EnforcementClient(base_url=..., token=...))`.
The recorder ships only the Protocol + no-op; the vendor's integration code (not
the recorder) wires the implementation.

---

## 2. The inline gate hook interface

**Contract (structural `typing.Protocol` so the recorder never imports the impl):**

- `GateRequest` (built from the `OpenSpan` at the pre-func seam): `span_type`,
  `attributes` (typed payload — `action_type`/`target_system` for side_effect,
  proposed artifact for human_approval), `compliance` (carries `end_customer_id`,
  `policy_version_active`, `regulatory_regime` — the tenancy + version keys),
  `trajectory_id`, `span_id`, `parent_span_id`, `name`, `inputs`.
- `Verdict` (frozen): `decision: Literal["allow","hold","block"]`, `reason`,
  `policy_name`, `policy_version`, `rule_id`, `rule_details: dict | None`,
  `hold_id: str | None` (set when `decision=="hold"`).
- `EnforcementGate` Protocol: `evaluate(req) -> Verdict` and
  `evaluate_async(req) -> Verdict` (no-op + client provide both; async path used
  by `_wrap_async` so the event loop isn't blocked).

**Decision → host-call semantics:**
- `allow` → run `func()` (also the fail-open/advisory/engine-pass path).
- `block` → do **not** run `func()`; raise host-visible `EnforcementBlocked`;
  emit `policy_check` `result="fail"` linked to the gated span. *The one
  deliberate violation of "agent always wins" — opt-in only.*
- `hold` → block inline, drive fail-to-human (§5); on `approved` → run `func()`,
  on `rejected`/`timeout(abort)` → raise `EnforcementBlocked`.

**Slots into `decorator.py` (three seams, one helper call each):**
- `_wrap_sync` — between `with span_scope(open_span):` (~170) and
  `result = func(...)` (~172). The existing `except BaseException` (~173) already
  closes the span with error on `EnforcementBlocked` and re-raises.
- `_wrap_async` — between ~209 and ~211, via `evaluate_async`.
- `traced.__enter__` (context-manager) — after `self._cm_scope_ctx.__enter__()`
  (~243), before `return open_span`; raising here means the user's `with` body
  (the side-effecting code) never runs.

**New ErrorCodes** (AC5xx band, added to `_internal/safelog.py` `ErrorCode`
StrEnum + `REMEDIATION`): AC501 gate raised internally; AC502 verdict service
unreachable/timeout; AC503 hold-resolution channel failed; AC504 emit-verdict
span failed.

---

## 3. Rule model: definition, versioning, evaluation, storage/loading

**Mirror `redaction/policy.py`** (frozen dataclass + `load_policy`/`parse_policy`
from bank-authored YAML, with a `version` field stamped onto spans). New module
`packages/enforcement/.../rules.py`:

- Bank-authored YAML, vendor-loaded. Sketch:
  ```yaml
  version: "enforcement-lending-v1.0.0"   # ties to ComplianceMetadata.policy_version_active
  default_action: advisory
  rules:
    - id: ecoa.aa_letter.requires_human_approval
      applies_to: { span_type: side_effect, action_type: ["document.mail"] }
      regulatory_regime: [ECOA, FCRA]
      evaluator: requires_prior_human_approval   # v1: immediate-span predicate
      mode: advisory                              # advisory | blocking (rollout gate)
      failure_mode: fail_to_human                 # fail_open | fail_to_human | fail_closed
      timeout_ms: 150                             # verdict-call budget
      hold_timeout_s: 3600                        # human-wait budget
      timeout_action: abort                       # abort | allow (on hold timeout)
      enabled: true
  ```
- `EnforcementRuleSet` (frozen): `version`, `default_action`, ordered `rules`,
  plus lookups `rules_for(span_type, action_type, end_customer_id)`.
- **Versioning**: the bundle `version` is the enforcement analogue of
  `Policy.version`; it is recorded on every emitted `policy_check`/verdict via
  `rule_details` and tied to the gated span's `compliance.policy_version_active`.
- **Storage/loading**: rules loaded vendor-side, scoped per `end_customer_id`
  (and per vendor). Loaded into the verdict service; a digest/version is exposed
  for audit. The local fallback table is derived from the ruleset (only
  `(end_customer_id, action_class) → failure_mode`).
- **Evaluation (v1)**: pure functions over the immediate `GateRequest` (no
  ledger lookups). Evaluator registry keyed by name (mirrors redaction strategy
  registry). Trajectory-aware evaluators are v2.

---

## 4. Failure-mode state machine (per-rule)

Advisory-only span types never enter this machine (they emit a `policy_check` and
always proceed). For the two gated types:

| Situation | `fail_open` | `fail_to_human` (default) | `fail_closed` (opt-in) |
|---|---|---|---|
| reachable + **pass** | ALLOW → run func | ALLOW → run func | ALLOW → run func |
| reachable + **fail** | advisory `policy_check` warn/fail, **run func** | **HOLD** → escalate (§5) | **BLOCK** → raise, func not run |
| **unreachable / timeout / gate raised (AC50x)** | advisory + **run func** | **HOLD** → escalate | **BLOCK** (uses local fallback table) |

Invariants:
- Common case (reachable + pass) adds only the verdict-call latency.
- The unreachable branch is decided **per-rule** by `failure_mode`, read from the
  **local fallback table** when the cloud is down — this is the only way a
  `fail_closed` contract survives an outage without shipping rule logic locally.
- Internal gate exceptions are treated identically to "unreachable."
- HOLD resolution: `approved` → run func; `rejected`/`aborted`/`timed_out(abort)`
  → `EnforcementBlocked`; `timed_out(allow)` → run func (per `timeout_action`).

Host-call outcome is always one of: *runs normally*, *runs after human approval*,
or *does not run + `EnforcementBlocked` raised*. Never an uncontrolled crash.

---

## 5. Fail-to-human escalation path

**Hold inline.** On `hold`, the gate does not run `func()`:
- **Async seam** (`_wrap_async`): `await` a resolution tied to `hold_id`; the
  event loop stays free. The clean path — recommend gated side-effects be async.
- **Sync seam**: long-poll the resolution endpoint until resolved or
  `hold_timeout_s`. Acceptable only because these steps are rare; **document
  loudly** that a sync side_effect under `fail_to_human` blocks a worker thread.

**Hold queue (engine-side table `enforcement_hold`)** — shape modeled on
`models.AccessLog`: `hold_id (uuid pk)`, `end_customer_id`, `trajectory_id`,
`span_id`, `parent_content_hash` (links to gated span), `policy_name`,
`policy_version`, `rule_id`, `proposed_action` (summary from attributes/inputs),
`status ∈ {pending,approved,rejected,timed_out,aborted}`, `created_at`,
`expires_at`, `resolved_at`, `approver_token_id`, `decision_reason`. Status
transitions are auditable (guarded update + audit row, consistent with the
ledger's append discipline). Hold keyed by `(trajectory_id, span_id, rule_id)`
for idempotency under recorder retries.

**Reviewer surface (v1 = API + queue only; UI deferred to L6):** REST in
`packages/enforcement`, reusing `ledger/.../api/auth.py` `Token` +
`require_role("reviewer")` + `require_customer_scope(token, end_customer_id)`:
- `GET /holds?status=pending` (scoped to caller's `end_customer_id`).
- `POST /holds/{hold_id}/resolve` `{decision, reason, approver_identity,
  approver_role, signature?}` — writes an `AccessLogger`-style audit row.
- `GET /holds/{hold_id}/resolution` — 200 with decision, 204 while pending (the
  blocked inline caller's rendezvous).

**Resume/abort.** The hold record is the rendezvous: the verdict service creates
it and returns `hold_id`; the reviewer resolves it; the blocked caller observes
resolution and the gate maps it to ALLOW (run func) or `EnforcementBlocked`.

**Timeout.** A worker (mirror `worker/retention_job.py:run_forever`) marks expired
pending holds `timed_out`; the caller applies the rule's `timeout_action`
(default `abort` = block, fail-safe for irreversible actions).

---

## 6. Run location per mode + latency / availability budgets

| Mode | Runs where | Budget |
|---|---|---|
| Advisory (all non-gated types) | **Ledger ingest** (`ingest.py:_validate_one`, after idempotency/linkage, before `sink.append()`); post-hoc, never blocks | none on host; ingest still returns 202 |
| Inline verdict (gated types) | **Recorder gate → cloud verdict service (sync)** | per-rule `timeout_ms` (default 150ms); on exceed → unreachable branch |
| Fail-to-human hold | Inline park (async) / thread-block (sync) + cloud queue | no latency budget; bounded by `hold_timeout_s` |
| Fail-closed | Inline; cloud verdict or local fallback table | same `timeout_ms`; outage still blocks via fallback |

**Availability:** the verdict service needs higher availability than ingest (it's
on the critical path); run multi-replica. The **local failure-mode fallback
table** degrades an outage *per-rule* instead of globally — fail-open/advisory
rules proceed, fail-closed rules still block. The table is failure-mode metadata
only (no rule content), refreshed opportunistically with a bounded-staleness SLA
(see risk R3).

---

## 7. Telemetry, alerts, verdict audit

- **Every advisory verdict** → a `policy_check` span: `policy_name`,
  `policy_version`, `result ∈ pass|fail|warn|not_applicable`, with rule_id /
  reason / failure_mode / engine-reachable flag in `rule_details`. Opened inside
  the gated span's scope so `builder.py` chains `parent_content_hash`
  automatically — tamper-evident tie, no schema change.
- **Every blocking/hold verdict** → `policy_check` (fail) + (on resolution) a
  `human_approval` span (`decision ∈ approved|rejected|escalated`).
- **Verdict audit** in the engine DB: an `AccessLogger`-style row per verdict
  (who/which rule/which span/outcome/when), scoped by `end_customer_id`.
- **Alerts/metrics** (mirror ledger Prometheus pattern): counters for
  verdicts by result, holds opened/resolved/timed-out, verdict-service
  latency/timeouts, fallback-table activations, `EnforcementBlocked` count.
- **Reads** (later, via L6/reporter): the reporter's `ledger_source` pattern
  extends to query verdicts/holds for "show me every action we blocked/held."

---

## 8. Tenancy & config model

- Rules are **bank-authored, vendor-enforced, versioned**, scoped per
  `end_customer_id` (and per vendor). Reuse `ledger/.../api/auth.py`
  `Token{token_id, role, end_customer_id}` + `require_customer_scope`. A new
  `reviewer` role gates the hold API; existing scoping ensures a tenant sees only
  its own holds/verdicts.
- Engine config via pydantic-settings (mirror `ledger/.../config.py`):
  `ENFORCEMENT_RULES_PATH`, `ENFORCEMENT_VERDICT_TIMEOUT_MS`,
  `ENFORCEMENT_HOLD_TIMEOUT_S`, `ENFORCEMENT_ADVISORY_AT_INGEST` (bool), etc.
- Recorder gate config (vendor-set at `set_gate`): service URL, token, per-call
  timeout, fallback-table path/refresh.

---

## 9. Safety & rollout

- **Global kill-switch:** the gate registry default is `None` → uninstalling
  enforcement is "don't register the gate," zero recorder code change, zero
  latency. Per-rule `enabled` + `mode (advisory|blocking)` allow a rule to ship
  live but non-blocking.
- **Advisory-first:** every rule lands in `mode=advisory` (records verdicts,
  always ALLOWs) before any bank opts a specific rule into hold/block.
- **Failure-injection tests** are first-class at each phase (service down, slow,
  gate raises) — must always preserve "host proceeds" except for explicit
  fail-closed.
- **Observability** of the gate path (latency, fallback activations, blocks) so
  rollout is measurable.

---

## 10. Dependencies on other layers & schema impact

- **Recorder (L1):** the only SDK change is the new `enforcement/gate.py` + the
  three one-line `_apply_gate` call sites in `decorator.py` + AC5xx error codes.
  Behavior is identical when no gate is registered (proved by running the
  existing suite unchanged).
- **Ledger (L2):** advisory-at-ingest hook in `ingest.py:_validate_one`; the
  engine reuses ledger auth/audit/worker/config patterns; new engine tables
  (`enforcement_hold`, verdict audit) live in the engine package (its own
  migrations), not the ledger's.
- **Reporting (L3)/Query (L6):** later consume verdicts/holds via the
  `ledger_source`-style client; the reviewer UI is an L6 concern.
- **Schema:** **no change in v1** (reuse `policy_check` + `human_approval` +
  `rule_details` + `parent_content_hash` + `policy_version_active`). A
  strongly-typed verdict record, if ever required by regulators, is a v2
  `SCHEMA_VERSION` **major** bump (breaks the ledger `schema_version_supported`
  major check in `ingest.py`; needs coordinated recorder+ledger rollout) — out
  of scope, documented as cost.

---

## 11. Phased build sequence & milestones

- **Phase 0 — Contract + no-op (recorder only).** `enforcement/gate.py`
  (Protocol, Verdict, registry, `_apply_gate` no-op, AC5xx); wire the three
  `decorator.py` seams behind `current_gate() and type in _GATED_TYPES`.
  *Milestone:* full existing test suite passes unchanged; a registered no-op gate
  is consulted only for the two gated types. Independently revertible.
- **Phase 1 — Advisory at ingest (cloud, no host impact).** `packages/enforcement`
  scaffold; rule model + loader (mirror `redaction/policy.py`); evaluator; hook
  into `ingest.py:_validate_one`. *Milestone:* fixtures of gated spans produce
  `policy_check` verdicts; ingest still 202s regardless.
- **Phase 2 — Inline verdict service, advisory mode (no blocking).** Verdict
  service endpoint + `EnforcementClient` (150ms timeout + local fallback table);
  vendor registers the client; all rules `mode=advisory` (call service, record
  verdict, always ALLOW). *Milestone:* full inline path exercised in prod with
  zero blocking risk; failure-injection (down/slow/raises) → host proceeds, AC50x
  logged.
- **Phase 3 — Fail-to-human (hold) for opted-in rules.** `enforcement_hold`
  table + review/resolve API (reuse `auth.py`) + timeout worker (mirror
  `retention_job`). Enable `hold` on one low-risk rule for one consenting bank;
  async seam first. *Milestone:* approve→func runs; reject→`EnforcementBlocked`;
  timeout→`timeout_action`; concurrent holds; reviewer auth/scope enforced; audit
  rows written.
- **Phase 4 — Fail-closed (block) per opted-in rule.** Flip a bank-chosen,
  vendor-consented rule to `fail_closed`. *Milestone:* rule fail →
  `EnforcementBlocked`, func not run, `side_effect.success=False`, `policy_check`
  fail; service unreachable + fail_closed → still blocks via fallback table;
  gated span chains correctly.

Each phase is independently revertible (unregister gate, or flip rule
`enabled`/`mode`).

---

## 12. Open questions / risks

- **R1. Sync-path thread blocking under hold** — a sync `side_effect` under
  `fail_to_human` blocks a worker thread for the human-review window. Acceptable,
  or require gated side-effects to be async? (Recommend: document + warn-log.)
- **R2. Who creates the hold record** — recommend the *verdict service* creates
  it and returns `hold_id`, so the recorder never writes to enforcement storage.
- **R3. Fallback-table staleness** — if a bank flips a rule to `fail_closed` and
  the cloud goes unreachable before the table refreshes, the gate fails-open on a
  rule that should block. Mitigation: conservative default, signed table, bounded
  staleness SLA. Needs design.
- **R4. `EnforcementBlocked` is a new host-visible exception** — the one
  deliberate "agent doesn't always win" case; requires explicit vendor consent
  capture + integration docs.
- **R5. Trajectory-aware rules (v2)** — v1 is immediate-span-only; ancestor-aware
  rules need the service to query the ledger. Scope boundary to hold.
- **R6. Verdict-span ordering on early abort** — confirm `builder.py` buffering
  produces the intended `parent_content_hash` chain when a block aborts the
  parent span early. Explicit test required.
- **R7. Dual use of `policy_version_active`** — redaction and enforcement both
  ride it; confirm one version string can encode both, else enforcement version
  stays in `rule_details` (avoids a schema bump).

---

## Verification (of the eventual implementation, per phase)
- **Phase 0:** `uv run pytest packages/python` green unchanged; new unit tests for
  registry + `_apply_gate` gating only the two types; assert no-gate path adds no
  calls (spy on `current_gate`).
- **Phase 1:** unit tests on rule loader (mirror redaction policy tests) + ingest
  hook tests asserting `policy_check` emission and 202.
- **Phase 2:** integration test with a stub verdict service; failure-injection
  matrix (down/slow/raise) all assert host proceeds; latency assertion under the
  150ms budget.
- **Phase 3:** end-to-end hold: open hold → resolve approve/reject/timeout →
  assert host outcome + `human_approval` span + audit row + tenant scoping.
- **Phase 4:** fail-closed e2e incl. outage-via-fallback-table; assert gated
  `side_effect.success=False`, `policy_check` fail, correct hash chaining.
- **Cross-cutting:** a recorder→(gate)→ledger e2e mirroring
  `tests/e2e/test_recorder_to_ledger_to_reporter.py`, plus `ruff`/`mypy --strict`
  clean per repo standard.
