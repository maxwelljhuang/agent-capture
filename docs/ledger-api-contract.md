# Ledger & enforcement API contract (for the dashboard / control plane)

**Contract v1.2 · span `schema_version` 1.0.0.** This is the source of truth for
anything that queries the ledger over the wire — primarily the **Kelp control
plane** (a separate repo, our cloud), which queries this repo's ledger **in
place per request and renders transiently, persisting no regulated payload**.
If you built against a mock, reconcile against this doc; it reflects the actual
FastAPI routes + Pydantic schema, not an assumed shape. v1.1 added the additive
dashboard surface; v1.2 adds time windowing on `/stats` (see the changelog at the
end); all earlier shapes are unchanged.

The two services a consumer talks to:
- **Ledger** (L2) — read API, port **8443**, RFC-7807 errors.
- **Enforcement** (L5) — hold-review API, **separate service**, port **8475**,
  own Postgres, plain-FastAPI errors.

> **Scope note.** This repo owns *only* this query/enforcement seam. The
> dashboard (frontend / BFF / federated query) is a separate repo and is not
> built here. See `CLAUDE.md`.

---

## 1. Topology & tenancy

- **One ledger per vendor cloud.** The vendor runs it; multiple banks live
  inside it as distinct `end_customer_id`s. There is **no `vendor_id`** anywhere
  in the API — `end_customer_id` is the only tenancy identifier.
- **The unit is a trajectory, not a "decision".** A trajectory
  (`trajectory_id`, 32-hex OTel trace id) is the directed tree of spans behind
  one outcome. There is no `decision`/`decision_id` object; the closest to a
  "decision" is the root `planner_step` span.
- **Network posture is the vendor's deployment choice.** Services bind plaintext
  behind a TLS-terminating ingress (mTLS / private-link / VPC-peering negotiated
  per vendor). Consumers reach the ledger over **HTTPS** at a vendor-exposed
  endpoint.

## 2. Authentication & scoping

- Header: `Authorization: Bearer <token_id>.<secret>`.
- Tokens are **DB-backed, Argon2-hashed**, minted once by CLI
  (`ledger token create --role <role> --customer <end_customer_id>`), revocable,
  vendor-issued. **No OIDC / JWT / short-lived tokens.**
- **Scope rides in the token**, not in query params. A token carries
  `{token_id, role, end_customer_id}`; there is **no `end_customer_id` query
  param** on read endpoints. Issue **one `reader` token per bank**.
- Roles: `ingest` (recorder→ledger writes), `reader` (dashboard/reporter reads),
  `admin` (null `end_customer_id`, cross-tenant). Enforcement uses two static
  env tokens (service + reviewer); the reviewer can be tenant-bound
  (`ENFORCEMENT_REVIEWER_CUSTOMER`).
- **Out-of-scope → `403` (`LE104`)**, never 404, never empty 200. `404`
  (`LE404`) means the resource genuinely doesn't exist. A non-admin token sees
  only its own `end_customer_id`; an `admin` token sees all tenants in that
  ledger. There is no cross-vendor access (each vendor = a separate ledger).
- Today there is **no admin-token-with-customer-filter** on the read endpoints
  (admin returns all tenants). If the control plane needs that, it's an additive
  API change (see §10).

## 3. Errors

- **Ledger** — RFC 7807 `application/problem+json`:
  `{ "type": "<uri>/<code>", "title", "status", "code", "detail"? }`.
- **Enforcement** — plain FastAPI: `{ "detail": "..." }`. (Handle both shapes.)
- Retryable: `503` (`LE201` backpressure, `LE202` DB unavailable). There is no
  built-in rate limiter or retry/backoff contract — apply your own.

| code | HTTP | meaning |
|---|---|---|
| LE001 | 422 | span shape invalid |
| LE002 | 403 | tenant mismatch (ingest) |
| LE003 | 422 | content-hash mismatch |
| LE004 | 409 | immutability violation |
| LE005 | 422 | parent-hash mismatch |
| LE006 | 422 | schema version unsupported |
| LE101 | 401 | auth missing |
| LE102 | 401 | auth invalid (bad/expired/revoked/unknown token) |
| LE103 | 403 | insufficient role |
| LE104 | 403 | tenant scope violation |
| LE201 | 503 | backpressure |
| LE202 | 503 | database unavailable |
| LE404 | 404 | resource not found |

## 4. Pagination

**Cursor-based, not offset.** List responses carry an opaque `next_cursor`
(base64 JSON); pass it back as `?cursor=`. There is **no `total`, no `offset`**.
Default sort is newest-first. `limit` defaults to 50 (trajectories max 500,
attestations max 200).

---

## 5. Span schema (`schema_version` 1.0.0)

The canonical source is the Pydantic models in
`packages/python/src/agent_capture/schema/`. Spans are returned **with raw,
post-redaction content** (`inputs`/`outputs`/`attributes`) — not a metadata-only
projection. Treat every span body as regulated content: render transiently,
persist nothing.

### 5.1 `SpanType` (exhaustive)
`model_call`, `tool_call`, `retrieval`, `planner_step`,
`sub_agent_invocation`, `human_approval`, `side_effect`, `policy_check`.

There is **no** `tool_result`, `model_output`, `decision`, or `redaction` type.
A `tool_call` carries its result in `outputs`; a `model_call` carries its
completion in `outputs`.

### 5.2 Core fields
| field | type | notes |
|---|---|---|
| `span_id` | str | 16-hex |
| `parent_span_id` | str \| null | null **only** for the trajectory root |
| `trajectory_id` | str | 32-hex; shared by all spans in the tree |
| `name` | str | |
| `type` | SpanType | matches `attributes.kind` |
| `start_time`,`end_time` | str | ISO-8601 **UTC, microsecond**, `…Z` |
| `status` | enum | **`ok` \| `error` \| `cancelled`** — no `completed`/`flagged` |
| `error` | obj \| null | `{error_type, message}` when `status=error` |
| `inputs`,`outputs` | any \| null | post-redaction payload, per-type shape |
| `attributes` | typed obj | per-type union, discriminated by `kind` |
| `compliance` | obj | §5.3 |
| `provenance` | obj | §5.4 |

### 5.3 `compliance` (exact fields — no `redacted` bool, no `labels` map)
`policy_version_active` (str), `prompt_template_version` (str?),
`model_card_version` (str?), `tool_schema_version` (str?), `agent_version`
(str), `end_customer_id` (str), `subject_id` (str?, **always HMAC-fingerprinted
at capture as of v1.1** — `[FP:…]`; surfaced as `subject_ref` on list rows, §6),
`regulatory_regime` (**list**), `retention_class`, `data_classification`.

- `regulatory_regime` ∈ `{ECOA, FCRA, SR_11-7, UDAAP, GLBA, BSA_AML, HIPAA,
  GDPR, CCPA}` — a per-span list; aggregate over the trajectory for a
  decision-level view.
- `retention_class` ∈ `{standard, extended, litigation_hold, transient}`.
- `data_classification` ∈ `{public, internal, PII, PCI, MNPI, PHI}`.

Per-type detail (model name/version/tokens, tool args, side-effect action_type,
policy_check result/rule_details, human_approval decision/approver) lives in
`attributes`, **not** in `compliance`. Derive dashboard fields from there.

### 5.4 `provenance` & integrity
`content_hash` (hex SHA-256, **no `sha256:` prefix**, `^[0-9a-f]{64}$`),
`parent_content_hash` (hex, null for root), `schema_version` (str).

`content_hash` = SHA-256 over the **canonical serialization**: `model_dump(json)`
with the `provenance` block **excluded**, keys sorted, compact separators
`(",", ":")`, `ensure_ascii=false`, no NaN/Inf, datetimes as microsecond `…Z`
strings, UTF-8. Python and TypeScript SDKs emit byte-identical bytes (golden
fixtures enforce it). The control plane should **display the hash as attested**
and may re-verify via §8.

### 5.5 Redaction (field-level, in-band)
Redaction happens in-process before persistence; the ledger never holds raw PII.
A redacted value is replaced inline in `inputs`/`outputs`/`attributes` with
`[REDACTED:<type>]` (full) or `[FP:<hmac-hex>:<type>]` (deterministic
fingerprint — equal values map to equal fingerprints). There is **no span-level
`redacted` flag and no `redaction_reason`/`policy`/`viewable_by` metadata**;
the only signals are the inline sentinels + `data_classification`. A
non-negotiable floor guarantees recognized PII (ssn, routing_number,
account_number, micr, date_of_birth) is never shipped in cleartext.

---

## 6. Ledger read endpoints (port 8443, `reader`/`admin` token)

Every read is access-logged server-side (actor token, role, `end_customer_id`,
action, target, your `x-request-id`, ip, ua, time).

### `GET /trajectories` — list
Params (all optional): `from`, `to` (ISO datetimes), `regime`, `type`, `status`,
`agent_version`, `cursor`, `limit` (≤500), and **`end_customer_id`** (v1.1 —
honored only for an `admin` token; a `reader` is always pinned to its own tenant).
```json
{ "items": [ { "trajectory_id": "…", "first_start": "…Z", "last_end": "…Z", "span_count": 7,
               "disposition": "clean" | "warn" | "violation",
               "regulatory_regime": ["ECOA","FCRA"],
               "subject_ref": "[FP:…:subject_id]" | null } ],
  "next_cursor": "…" | null }
```
Sort: `first_start` desc, then `trajectory_id` desc. **v1.1** added the
`disposition`, `regulatory_regime`, and `subject_ref` row fields. `subject_ref`
is non-null **only** when the root span's `subject_id` is a fingerprint
(`[FP:…]`) — raw/None → `null`, so a possibly-PII value is never surfaced.

### `GET /trajectories/{trajectory_id}` — detail
```json
{ "trajectory_id": "…", "end_customer_id": "acme-bank", "span_count": 7,
  "first_start": "…Z", "last_end": "…Z",
  "chain_status": "verified" | "incomplete:pending_parents" | "broken",
  "disposition": "clean" | "warn" | "violation" }
```
`disposition` (v1.1): `violation` if any `policy_check` result is `fail`; `warn`
if any `warn` and no `fail`; else `clean`.

### `GET /trajectories/{trajectory_id}/spans` — full spans
```json
{ "trajectory_id": "…", "spans": [ { …full span (§5)… } ] }
```
Note: `end_customer_id` is **not** in this envelope (scope enforced via token).

### `GET /spans/{span_id}?trajectory_id=…` — one span
`trajectory_id` is required (span_id alone isn't unique across partitions).
Returns the bare span object.

### `GET /access-log` — read-audit (v1.1)
Who queried the ledger — **metadata only**, tenant-scoped, cursor-paginated.
Params: `from`, `to`, `cursor`, `limit` (≤500), `end_customer_id` (admin-only filter).
```json
{ "items": [ { "access_id": "…", "actor_token_id": "…", "actor_role": "reader",
               "end_customer_id": "acme-bank", "action": "read.trajectory",
               "target_kind": "trajectory", "target_id": "…", "at": "…Z",
               "request_id": "…" | null, "ip": "…" | null, "user_agent": "…" | null } ],
  "next_cursor": "…" | null }
```
Note: the ledger only ever holds **redacted** data, so this is "who queried the
record," not "who viewed unredacted PII." Reading it is itself audited.

### `GET /stats` — aggregate counts + dashboard metrics
Params: `from`, `to`, `end_customer_id` (admin-only filter), and **`window`**
(v1.2 — relative window, e.g. `24h`, `7d`, `4w`).
```json
{ "span_count": 0, "trajectory_count": 0, "by_status": {…}, "by_type": {…},
  "trajectory_volume": 0,
  "by_disposition": { "clean": 0, "warn": 0, "violation": 0 },
  "violation_count": 0,
  "coverage_by_regime": { "ECOA": 0, "FCRA": 0 },
  "controls": [ { "regime": "ECOA", "key": "adverse_action",
                  "label": "Adverse-action decision recorded",
                  "passing": 218, "total": 220, "last_evaluated": "…Z",
                  "status": "pass" | "attention" } ],
  "window": "7d" | null,
  "previous": {
    "trajectory_volume": 0, "violation_count": 0,
    "by_disposition": { "clean": 0, "warn": 0, "violation": 0 },
    "coverage_by_regime": { "ECOA": 0, "FCRA": 0 } } }
```
**v1.1** added `trajectory_volume`, `by_disposition`, `violation_count`,
`coverage_by_regime`, and `controls`. The dashboard aggregates live here on
`/stats` (not a separate `/metrics` — that path is the Prometheus endpoint).
`controls[]` is computed from a catalog (`status` = `pass` when
`passing == total`, else `attention`); the built-in default covers ECOA/FCRA
(`adverse_action`, `consumer_report`, `model_rationale`, `human_review`) and is
overridable via `LEDGER_CONTROLS_PATH` (YAML).

**v1.2 — time windowing (P7).** Pass `window` (`24h`/`7d`/`4w`) and/or `from`/`to`
(ISO) to scope the **dashboard aggregates** to trajectories whose **`first_start`**
falls in the window (half-open `[from, to)`; `window` resolves to `[now − window, now]`).
When a finite window is active the response adds **`window`** (the relative label,
or `null` for explicit `from`/`to`) and **`previous`** — the *count-style*
aggregates (`trajectory_volume`, `violation_count`, `by_disposition`,
`coverage_by_regime`; no `controls`) over the immediately-preceding equal-length
window. Derive trend deltas as `current − previous`. **Omitting `window`/`from`/`to`
returns the exact v1.1 all-time shape** (no `window`/`previous` keys). Empty
windows return zeroed aggregates (not an error); an invalid `window`/`from`/`to`
(bad format, or `from > to`) → **`400` `LE007`** in the standard problem shape.
Tenant scope is the reader token's bank — never widened by the request. Note the
basic counts (`span_count`/`trajectory_count`/`by_status`/`by_type`) still window
on span `start_time`, unchanged from v1.1.

Still **not** provided: `pending_review_count` (it's enforcement-side — use
`GET /holds/count`, §7), `alert_count` (no definition).

## 7. Enforcement endpoints (port 8475, separate service & DB)

The hold queue lives in the **enforcement service**, not the ledger.

### `GET /holds?end_customer_id=<tenant>` — pending holds (reviewer token)
Returns a **bare array** (no `{items}` wrapper):
```json
[ { "hold_id": "…", "end_customer_id": "acme-bank", "trajectory_id": "…",
    "span_id": "…", "rule_id": "review_wire", "policy_version": "enf-v1",
    "proposed_action": "…", "reason": "…", "status": "pending" } ]
```
Field map from a typical mock: `action_id`→`hold_id`, `decision_id`→
`trajectory_id`, `action_class`→`proposed_action` / rule `action_type`.

### `POST /holds/{hold_id}/resolve` — approve/reject (reviewer token, tenant-scoped)
Body: `{ "decision": "approved" | "rejected", "decision_reason"?: str, "approver_identity"?: str }`
(note: `approved`/`rejected`, not `approve`/`reject`). Returns the updated hold.

> **This writes only to the enforcement DB.** It does **not** write a
> `human_approval` span to the ledger and returns **no** span id. The
> `human_approval` span is emitted **agent-side** when the recorder's gate
> unblocks. **Not idempotent**: re-resolving a terminal hold → `409`.

### `GET /holds/count?end_customer_id=<tenant>` — pending count (v1.1, reviewer token)
Cheap tile source — avoids paging the array. Tenant-scoped (403 across tenants).
```json
{ "pending": 3 }
```

### `GET /holds/{hold_id}/resolution` — poll (service token; recorder uses this)
```json
{ "hold_id": "…", "status": "pending|approved|rejected|timed_out|aborted",
  "decision": "allow" | "block" | null }
```

### Other enforcement facts
- `POST /verdict` is called by the **recorder client only**, not the control
  plane.
- Rules are **file-based YAML** (`ENFORCEMENT_RULES_PATH`, hot-reloaded);
  `mode ∈ {advisory, blocking}`, `failure_mode ∈ {fail_open, fail_to_human,
  fail_closed}`. There is **no HTTP rule-config push API**.
- There is **no first-class "alert" or "violation"** object. A "violation" is
  best derived from a `policy_check` span with `attributes.result ∈ {fail,
  warn}`.

## 8. Integrity / verification

- `POST /verify/{trajectory_id}` — recompute every hash + chain:
  ```json
  { "trajectory_id": "…", "spans": 7, "status": "verified" | "broken",
    "chain_intact": true,
    "findings": [ { "span_id": "…", "kind": "content_hash_drift|root_has_parent_hash|missing_parent|parent_hash_mismatch",
                    "stored"?: "<hex>", "recomputed"?: "<hex>" } ] }
  ```
- `GET /attestations`, `GET /attestations/{id}`,
  `GET /attestations/proof/{trajectory_id}` — signed Ed25519 Merkle roots +
  inclusion proofs. A consumer can verify a trajectory against the published
  public key independently of the database (tamper-evidence even if the DB is
  doctored).
- `GET /litigation-holds` — admin-only list of placed holds.

## 9. Health / ops

Ledger (all unauthenticated): `GET /health` (`{status:"ok"}`), `GET /ready`
(DB + migrations; 503 if not ready), `GET /version`
(`{version, schema_version_supported}`), `GET /metrics` (Prometheus).
Enforcement: `GET /health`.

Span breaking changes are rejected at ingest by major-version check (`LE006`);
`GET /version.schema_version_supported` advertises the supported major.

## 10. Delivered vs still pending

**Delivered (v1.1):** dashboard aggregates on `/stats` (`trajectory_volume`,
`by_disposition`, `violation_count`, `coverage_by_regime`, **`controls[]`** with
a config-driven catalog); trajectory `disposition`; list `regulatory_regime` +
`subject_ref` (always-fingerprinted subject); enforcement `GET /holds/count`;
read-only `GET /access-log`; admin-token `?end_customer_id=` filter.

**Delivered (v1.2):** `/stats` time windowing — `window`/`from`/`to` + a
`previous` equal-length period for trend deltas (P7).

**Still pending (request when needed):**
- **Control catalog tuning** — `/stats.controls` ships a default ECOA/FCRA
  catalog (span-presence predicates); refine the predicates/labels by sending a
  `LEDGER_CONTROLS_PATH` YAML. The current predicates are a reasonable starting
  interpretation, not a compliance-blessed set.
- **Authenticated reviewer identity** — `approver_identity` on hold-resolve is
  still self-asserted (shared reviewer token). Per-reviewer-token auth is a
  planned token-model change.
- **Structured redaction metadata** (reason/policy/category/viewable-by) — a
  frozen-schema (v2) change; deferred.
- **Full time-series** (bucketed counts over time) — not built. Period-over-period
  trend (current vs `previous`) **is** available via `/stats` windowing (v1.2);
  a bucketed series would be a further addition.
- **Rules-config push API** for enforcement — file-based only.

---

*Changelog:*
- **v1.2 (schema 1.0.0)** — `/stats` time windowing (P7): optional `window`
  (`24h`/`7d`/`4w`) and/or `from`/`to` scope the dashboard aggregates to
  trajectory `first_start`, with a `previous` equal-length period for trend
  deltas and a `window` echo. Omitting them returns the v1.1 all-time shape
  unchanged; invalid input → `400 LE007`. Additive.
- **v1.1 (schema 1.0.0)** — additive dashboard surface: `/stats` aggregates +
  config-driven `controls[]`, trajectory `disposition`, list
  `regulatory_regime`/`subject_ref` (+ subject_id always fingerprinted at
  capture), `/holds/count`, `/access-log`, admin `?end_customer_id=` filter. No
  v1 shapes changed.
- **v1 (schema 1.0.0)** — initial contract published for the dashboard repo to
  replace its mock.
