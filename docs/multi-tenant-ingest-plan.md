# Multi-tenant ingest — implementation plan (#63)

The single biggest blocker from the `ai-employee` integration: a **multi-tenant
process can't ship to the ledger out of the box.** Each ingest token is pinned
to one `end_customer_id`, and the CLI mandates `--customer`, so one token serves
one tenant — but the consumer runs **one process for many tenants**, stamping
`end_customer_id = tenant_id` per span. They hand-rolled a ~70-line
`TenantRoutingExporter`. **Plan only — no implementation yet.**

> Note: the GitHub issue bodies for #63/#64 are mislabeled (each shows the next
> topic's text). This plan follows `docs/sdk-enhancement-suggestions.md §1`, the
> authoritative integration write-up, which defines #63 as below.

## Two complementary fixes (the doc proposes both)
- **Part A — Unscoped ingest token (ledger).** One trusted first-party token that
  may ingest spans for *any* tenant. Smallest unblock: the consumer uses a single
  `HTTPExporter`, stamps `end_customer_id` per span, and needs **no new SDK
  exporter**. Removes per-tenant token churn entirely.
- **Part B — `TenantRoutingExporter` (SDK).** For vendors who want **per-tenant
  tokens** (isolation, independent revocation, per-tenant rate limits): routes
  each span by `span.compliance.end_customer_id` to a per-tenant pipeline.

They're independent and solve different needs; A is the lighter, ship-first path.

## Confirmed ground truth (verified against the code)
- `api/routes/ingest.py:183` — the tenant check is **skipped when
  `token.end_customer_id` is falsy**: `if token.end_customer_id and
  span.compliance.end_customer_id != token.end_customer_id: reject(LE002)`. So an
  unscoped (null-customer) ingest token already accepts spans for any tenant, and
  each span is stored under **its own** `end_customer_id`. ✔
- `cli/token.py` — `if role != "admin" and not customer: raise "--customer
  required for ingest/reader"` is the **only** thing blocking an unscoped ingest
  token. ✔
- `exporter/base.py` — `SpanExporter` Protocol = `export(span)` + `shutdown(timeout)`;
  `flush()` was added to the concrete `BoundedQueueExporter`/`HTTPExporter` in #62.
- `exporter/http.py` — `HTTPExporter(endpoint, *, auth_token=None, batch_size=100,
  batch_max_wait_s=1.0, retry_policy=..., timeout_s=10.0, client=None)`.

---

## Part A — Unscoped ingest token (ledger)

**CLI.** Allow `ledger token create --role ingest` with **no `--customer`**, gated
behind an explicit **`--unscoped`** flag so it's never accidental:
- `--role ingest --unscoped` → mints an ingest token with `end_customer_id = NULL`.
- `--role ingest` without `--customer` and without `--unscoped` → still rejected
  (unchanged, prevents accidental footgun).
- `--unscoped` is only valid with `--role ingest` (reject for reader; admin is
  already unscoped by nature).

**Ingest path.** No change needed — already accepts it. One cosmetic detail:
`ingest_batch.end_customer_id = token.end_customer_id or ""` (`ingest.py:112`)
records `""` for an unscoped token's batch row; spans still store their own
`end_customer_id`. Acceptable (batch tracking only); optionally store `NULL`.

**Security posture (the one real trade-off).** An unscoped ingest token can write
spans attributed to **any tenant** — it widens ingest-side trust. It is a
**trusted first-party credential** for the vendor's own multi-tenant process:
- Document: short rotation, never hand to a third party, treat like the service's
  primary secret.
- Reads are unaffected — reader tokens stay pinned per `end_customer_id`.
- Hash-chain / attestation integrity is unaffected (a forged span is still a
  valid-shaped span; this is about *attribution* trust, which the vendor already
  holds for its own process).

**Tests.**
- mint an unscoped ingest token; POST `/spans` with two different
  `end_customer_id`s through it → both **accepted** and stored under their own
  tenant.
- `--unscoped` mints a NULL-customer ingest token; `--role ingest` without
  customer/unscoped still errors; `--role reader --unscoped` errors.

**Docs.** `key-management.md` (unscoped ingest token + rotation), `deployment.md`
+ `integration-guide.md` (multi-tenant: one unscoped token, stamp
`end_customer_id` per span).

---

## Part B — `TenantRoutingExporter` (Python SDK)

New `agent_capture/exporter/routing.py` exposing `TenantRoutingExporter`
(a `SpanExporter`). Routes each span by `span.compliance.end_customer_id` to a
per-tenant inner exporter, built **lazily** via a factory and cached.

**API.**
```python
TenantRoutingExporter(factory: Callable[[str], SpanExporter | None])
# convenience builders:
TenantRoutingExporter.from_tokens(endpoint, tokens: Mapping[str, str], **http_kwargs)
TenantRoutingExporter.from_token_provider(endpoint, provider: Callable[[str], str | None], **http_kwargs)
```
- `factory(tenant_id)` → a built pipeline (or `None` = no token → drop+log). Most
  general; supports onboarding-time token refresh and custom pipelines.
- `from_tokens` / `from_token_provider` default each tenant's pipeline to
  `BoundedQueueExporter(HTTPExporter(endpoint, auth_token=token))` so backpressure
  is **per-tenant isolated**.

**Behavior.**
- Lazy build + cache per tenant, guarded by a `Lock` (the recorder may export from
  multiple threads).
- Unknown tenant (`factory` → `None`): increment a `dropped_count`, log `AC4xx`
  once per tenant, **never raise** (cardinal rule — the agent always wins).
- `export` / `shutdown` / `flush` fan out to all live inner exporters; `shutdown`
  and `flush` collect errors and never propagate into the host. (`flush` mirrors
  the #62 addition so serverless `flush_capture()` works through the router.)
- Re-export from `exporter/__init__.py`.

**Tests.** routes 2 tenants to 2 capture exporters; unknown tenant dropped +
counted; `flush`/`shutdown` fan out to all; concurrent lazy build is race-free;
an inner exporter raising never escapes. (recorder unit tests, no network.)

**Docs.** `integration-guide.md` multi-tenant section (Model B: per-tenant tokens).

---

## Decisions to confirm before building
1. **Scope** — ship **A only** (lightest; lets the consumer delete their hand-rolled
   exporter today), **B only**, or **both**? *Recommend both, A first.*
2. **Unscoped UX** — explicit `--unscoped` flag *(recommended)* vs allowing
   `--customer ""`.
3. **TS parity for B** — defer until a TS multi-tenant consumer exists *(recommended;
   the consumer is Python, and Part A is language-agnostic)* vs build now.
4. **Routing ownership** — factory returns a full per-tenant pipeline *(recommended;
   per-tenant backpressure)* vs the router owning one shared queue.

## Phasing
1. **Part A** — ledger `--unscoped` ingest token + tests + docs. Small; unblocks
   the integration immediately (consumer drops its workaround, uses one token).
2. **Part B** — `TenantRoutingExporter` (Python) + tests + integration-guide.
3. *(optional)* TS parity for B; per-tenant dropped-span metrics; token_provider
   refresh hardening.

## Verification
- Part A: ledger integration test (one unscoped token ingests two tenants) + CLI
  unit tests; an e2e variant of `recorder→ledger` using an unscoped token across
  two tenants.
- Part B: recorder unit tests (routing / drop / fan-out / thread-safety /
  never-raise).
- Repo standard throughout: `ruff` + `mypy --strict` clean (+ TS gate if touched).
