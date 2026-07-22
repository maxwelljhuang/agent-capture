# Ledger additive API — implementation plan (contract v1 → v1.1)

Spec for the dashboard/control-plane additions requested in
`kelp-dashboard/docs/ledger-additive-requests.md` (the §10 gaps of
`docs/ledger-api-contract.md`). **This is a plan — no implementation yet.**
Everything here is **additive and backward-compatible**: new endpoints + new
*nullable* fields, bumping the consumer contract **v1 → v1.1**. The frozen span
`schema_version` (1.0.0) does **not** change except where P4 is explicitly
called out as a major-version cost.

## Locked decisions (from review)
1. **Disposition rule.** A trajectory's `disposition` is derived from its
   `policy_check` spans (`attributes.result ∈ {pass, fail, warn, not_applicable}`):
   - `violation` — any policy_check `result == "fail"`
   - `warn` — any `result == "warn"` and no `fail`
   - `clean` — otherwise (`not_applicable` ignored; a trajectory with no
     policy_check spans is `clean`)
   Computed ledger-side; deterministic.
2. **`subject_ref` non-PII guarantee.** Extend the redaction floor so
   `compliance.subject_id` is **always HMAC-fingerprinted at capture** (both
   SDKs). Once guaranteed non-PII, the list surfaces it directly as
   `subject_ref` — no separate field needed. (See §P2 for the migration caveat.)

## Honest corrections (already fed back to Kelp; baked into this plan)
- **`pending_review_count` is not in ledger `/metrics`** — the ledger can't read
  the enforcement DB. It comes from enforcement `GET /holds/count` (§P3).
- **`alert_count` is omitted** — no "alert" concept exists; only `violation` is
  derivable. Not built unless a definition is supplied later.
- **`approver_identity` is not authenticated today** — it's caller-supplied free
  text under a shared reviewer token. §P3 plans the honest fix (per-user reviewer
  auth) separately; until then the field is "self-asserted," surfaced as such.

---

## P0 — `GET /metrics` (ledger; reader = per-bank scope)

Params: `from`, `to` (optional). Auth: `reader` (its `end_customer_id`) or
`admin` (all, or one tenant once P6 lands). Shape:
```json
{
  "trajectory_volume": 1234,
  "by_disposition": { "clean": 1200, "warn": 22, "violation": 12 },
  "violation_count": 12,
  "coverage_by_regime": { "ECOA": 220, "FCRA": 220, "SR_11-7": 18 },
  "controls": [
    { "regime": "ECOA", "key": "adverse_action_reasons",
      "label": "Adverse-action reasons recorded",
      "passing": 218, "total": 220,
      "last_evaluated": "2026-06-06T12:00:00Z", "status": "pass" }
  ]
}
```
- `by_disposition` / `violation_count`: GROUP BY over the per-trajectory
  disposition (§disposition + §performance).
- `coverage_by_regime`: count of **trajectories** whose spans carry each
  `regulatory_regime` (a trajectory counts once per distinct regime present).
- `controls[]`: see the open sub-decision below. `status = "pass"` when
  `passing == total` and not stale; else `"attention"`.
- **Not included:** `pending_review_count` (→ enforcement `/holds/count`),
  `alert_count` (no definition).

### Open sub-decision — the `controls[]` catalog
`passing/total per control` needs a *defined* catalog of checks. Proposed:
**config-driven YAML** (`LEDGER_CONTROLS_PATH`, with a built-in default set) so
the catalog evolves without code. Each control is a predicate over a trajectory:
`scope` (which trajectories it applies to, e.g. by regime/type) + `pass_when`
(e.g. a `policy_check` of a given `policy_name` with `result == pass`, or
presence of a required span/field). Minimal starter set to propose:
- `ECOA / adverse_action_reasons` — adverse-action trajectories whose
  `model_call.outputs` carry a primary reason.
- `ECOA / protected_class_check` — a `policy_check` named `ecoa.protected_class.*`
  passed.
- `FCRA / cra_disclosure` — a `retrieval.source_identifier` (CRA) is present.
- `SR_11-7 / model_card_present` — every `model_call` carries
  `compliance.model_card_version`.
- `side_effect / human_approval_present` — gated side-effects have a preceding
  `human_approval`.

**Confirm the catalog approach (config-driven vs hardcoded starter) and the
starter set before P0 is built.** If undecided, P0 ships counts +
`coverage_by_regime` first and `controls[]` follows.

## P1 — Trajectory `disposition` (ledger; no schema change)

Add a nullable `disposition: "clean" | "warn" | "violation"` to:
- `GET /trajectories` list rows (§P2)
- `GET /trajectories/{id}` detail
Derived by the locked rule. This is the keystone — P0 `violation_count` and the
P2 list badge both read it.

## P2 — Trajectory-list enrichment (ledger)

Add nullable fields to each `GET /trajectories` row (and detail where natural):
- `disposition` (P1)
- `regulatory_regime`: aggregated distinct list across the trajectory's spans.
- `subject_ref`: the (now-always-fingerprinted) `compliance.subject_id`.

**`subject_ref` requires the capture change in locked-decision #2:**
- Extend the redaction floor so `subject_id` is HMAC-fingerprinted in-process
  (both Python + TS SDKs), like the existing PII floor. `subject_id` stays the
  same schema field; its *value* is always a fingerprint going forward.
- **Caveat:** the guarantee holds only for spans captured **after** this lands.
  Pre-existing spans may carry a raw `subject_id`; the ledger will return
  `subject_ref: null` for trajectories whose root span predates the change (or
  whose `subject_id` doesn't match the fingerprint shape), so Kelp never renders
  a possibly-PII value. Document this boundary.
- Touches `content_hash` (subject_id is in the canonical bytes) — but only for
  new spans, so no re-hash of history; golden fixtures get regenerated.

## P3 — Pending-review count + reviewer identity (enforcement)

- **`GET /holds/count?end_customer_id=`** (reviewer token, tenant-scoped) →
  `{ "pending": N }`. Trivial; avoids paging the array for a tile.
- **Reviewer identity (honest fix).** Today `approver_identity` is unauthenticated
  free text. Plan: bind the reviewer token to an identity the same way we bound
  it to a tenant — i.e. move toward **per-reviewer tokens** (token → reviewer id)
  so the recorded approver is the authenticated caller, not a self-asserted
  string. Until that lands, expose `approver_identity` but label it
  "self-asserted." (This is a token-model change; scope it as its own step.)

## P4 — Structured redaction metadata (deferred; schema cost)

`{ data_category, policy_basis, viewable_by_roles }` per redaction would let the
reviewer see *why* something was withheld. This is **not additive at the schema
level** — it adds fields the recorder must capture, which is a **frozen-schema
(`schema_version`) major change** requiring coordinated recorder + ledger + TS
rollout (and re-instrumentation for vendors). Kelp marked it low priority and can
ship the sentinel-based panel without it. **Recommend deferring**; if pursued,
plan it as a schema v2 effort, separately.

## P5 — Read-only access-log endpoint (ledger; easy)

The `access_log` table already records every read (actor token, role,
`end_customer_id`, action, target, `x-request-id`, ip, ua, time). Add
`GET /access-log?from&to&cursor` (reader = own tenant, admin = all;
cursor-paginated, metadata-only). Caveat to document: the ledger only ever holds
**redacted** data, so this is "who queried the (redacted) record," not "who
viewed unredacted PII" (unredaction lives in the bank's secure store, outside
this repo).

## P6 — Admin-token customer filter (ledger; easy)

Honor an optional `?end_customer_id=` on the read + `/metrics` endpoints **for
`admin` tokens only** (a `reader` token ignores it — its own scope wins). Lets a
vendor team view one bank without per-bank token fan-out. Low risk; reader
scoping is unchanged.

---

## Performance note (disposition + metrics at scale)

`disposition`, `coverage_by_regime`, and `controls[]` are per-trajectory rollups
over `policy_check`/regime data scattered across a trajectory's spans. Two
approaches:
- **On-the-fly SQL** (start here) — correct and simple; fine for list pages and
  windowed `/metrics`. Risk: a full-tenant `/metrics` with no `from/to` scans a
  lot.
- **Materialized per-trajectory rollup** (scale path) — a `trajectory_summary`
  table (`trajectory_id, end_customer_id, disposition, regimes[], subject_ref,
  first_start, last_end, span_count`) maintained at ingest. Makes list +
  `/metrics` index-only.
Plan: build on-the-fly first behind the same response shapes; introduce the
rollup table if/when `/metrics` latency demands it — invisible to the contract.

## Phased build sequence (when approved)

1. **P1 disposition** (ledger) — derivation + expose on detail; unit + integration tests. Keystone.
2. **P2 enrichment** — list `disposition` + `regulatory_regime`; **then** the `subject_id` always-fingerprint capture change (both SDKs, floor + golden-fixture regen) + `subject_ref` surfacing with the pre-change `null` boundary.
3. **P0 `/metrics`** — counts + `coverage_by_regime` first; `controls[]` after the catalog sub-decision; (optional) materialized rollup if needed.
4. **P3** — `/holds/count` (quick), then the per-reviewer-token identity change (separate).
5. **P5** `/access-log`, **P6** admin filter — cheap wins, any time.
6. **P4** — only if pursued, as a schema-v2 effort.

## Verification (per step)
- Disposition: unit tests over policy_check fixtures (fail/warn/none/mixed); an integration test asserting list + detail + `/metrics` agree.
- subject_ref: SDK floor tests (subject_id fingerprinted in both Python + TS); ledger returns `null` for pre-change spans; golden fixtures regenerated; Py/TS byte-parity holds.
- /metrics: aggregate tests vs a seeded multi-disposition, multi-regime dataset; tenant scoping (reader sees only its own; admin all / filtered).
- /holds/count, /access-log, admin filter: scoped-auth + count/pagination tests.
- Repo standard throughout: `ruff` + `mypy --strict` + the TS gate clean.

## Contract impact
Publish **contract v1.1** (additive) on merge: new `/metrics`, `/access-log`,
enforcement `/holds/count`; new nullable `disposition`, `regulatory_regime`,
`subject_ref` on trajectory rows; admin `?end_customer_id=`. No removals, no
breaking changes. P4 (if ever) would be the first thing to force a v2.
