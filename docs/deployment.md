# Deployment runbook (vendor cloud)

How to stand up the two services this repo ships — the **ledger** and the
**enforcement engine** — in a vendor's cloud, securely. The **recorder SDK**
(`agent-capture`) and **reporter** are libraries; see
`docs/integration-guide.md` and `packages/reporter/README.md`.

Companion docs: **`docs/key-management.md`** (keys/secrets), **`docs/backup-dr.md`**
(backup/restore/DR), **`SECURITY.md`** (security policy + TLS posture). Just
kicking the tires? Start with **`docs/tester-quickstart.md`** — clone → queried
trajectory in ~15 min, from source.

> Fast path for a VM/dev box: `docker compose -f packages/ledger/docker/docker-compose.yml up`
> and `docker compose -f packages/enforcement/docker/docker-compose.yml up` bring
> each service up (migrating on start). The steps below are the production path.

## Prerequisites
- Postgres 16 (one database per service; can share a cluster).
- A TLS-terminating ingress (services bind plaintext — terminate at the ingress;
  see `SECURITY.md`).
- A secret store (K8s Secret / vault). Never commit real secrets; templates in
  `deploy/k8s/*.example.yaml` and `packages/*/.env.example`.

---

## Part 1 — Ledger (L2)

1. **Database + roles.** The ledger uses four least-privileged roles
   (`ledger_app` INSERT/SELECT, `ledger_reader` SELECT, `ledger_retention`
   DELETE, `ledger_attestation`). Create them + the DB, then:
   ```bash
   # creates roles + runs migrations (needs a DBA DSN)
   LEDGER_DATABASE_URL=postgresql+asyncpg://postgres:…@db/ledger ledger db init
   ```
   Or, if roles already exist (e.g. `initdb.sql`): `ledger db migrate`
   (set `LEDGER_DATABASE_URL_MIGRATE` to a role with CREATE on `public` — the
   runtime `LEDGER_DATABASE_URL` is intentionally least-privileged).
2. **Signing key.** Generate the Ed25519 attestation keypair and set
   `LEDGER_SIGNING_KEY_PATH` / `LEDGER_SIGNING_KEY_ID` — see `docs/key-management.md`.
   Publish the public PEM so regulators can verify attestations.
3. **Mint API tokens** (Argon2-hashed, shown once):
   ```bash
   ledger token create --role ingest --customer <end_customer_id>   # recorder → ledger
   ledger token create --role reader --customer <end_customer_id>   # dashboard/reporter reads
   # multi-tenant process: one unscoped token instead of one per tenant —
   ledger token create --role ingest --unscoped                     # ingests ANY tenant (key-management.md)
   ```
4. **Configure** via env / Secret (`LEDGER_*`, see `packages/ledger/.env.example`):
   runtime DSN, retention DSN, signing key, attestation sink, retention windows,
   `LEDGER_TLS_*` if terminating in-process (otherwise leave unset + use ingress).
5. **Start** (separate processes):
   ```bash
   ledger serve                 # API (listen_port 8443)
   ledger worker anchor         # periodic Ed25519 Merkle attestations
   ledger worker retention      # retention enforcement
   ```
6. **Verify:**
   ```bash
   curl -fsS https://ledger/ready        # {"status":"ready", schema_version:…}
   # ingest a span batch (recorder HTTPExporter shape), then:
   curl -H "Authorization: Bearer <reader>" https://ledger/trajectories
   curl -XPOST -H "Authorization: Bearer <reader>" https://ledger/verify/<tid>   # chain_intact: true
   ```

---

## Part 2 — Enforcement (L5)

1. **Database + schema** (its own Postgres DB):
   ```bash
   ENFORCEMENT_DATABASE_URL=postgresql+asyncpg://…@db/enforcement enforcement db migrate
   ```
   (`enforcement db init`/create_all is dev-only.)
2. **Rules.** Place the bank-authored rule YAML and point at it via
   `ENFORCEMENT_RULES_PATH` (validate first: `enforcement check-rules rules.yaml`).
   All rules should ship `mode: advisory` until you opt a specific rule into
   blocking — see `docs/enforcement-plan.md` §9.
3. **Tokens.** Set `ENFORCEMENT_SERVICE_TOKEN` (the recorder client presents it)
   and `ENFORCEMENT_REVIEWER_TOKEN` (the hold-review API). See key-management.
   Bind the reviewer to its tenant with `ENFORCEMENT_REVIEWER_CUSTOMER=<end_customer_id>`
   so it can only list/resolve that tenant's holds; leave it unset only for an
   admin/single-tenant deploy (the service warns at startup if a reviewer token
   is set without a tenant binding).
4. **Configure** via env / Secret (`ENFORCEMENT_*`, see
   `packages/enforcement/.env.example`): DB URL, rules path, tokens, timeouts.
5. **Start** (separate processes, same image):
   ```bash
   enforcement serve            # verdict service (listen_port 8475)
   enforcement timeout-worker   # expires pending fail-to-human holds
   ```
6. **Verify:**
   ```bash
   curl -fsS https://enforcement/health
   # a verdict round-trip (POST /verdict with a gated-span body) returns allow|hold|block
   ```
7. **Wire the recorder** (in the vendor's agent app, at startup):
   ```python
   from agent_capture.enforcement import set_gate
   from agent_capture_enforcement.client import EnforcementClient
   set_gate(EnforcementClient(base_url="https://enforcement", token="<service_token>"))
   ```
   Optionally enable advisory-at-ingest on the ledger by setting
   `LEDGER_ENFORCEMENT_RULES_PATH` (the ledger guards the optional import).

---

## Part 3 — Recorder SDK + reporter (libraries)
- **Recorder:** `pip install agent-capture` in the vendor's agent; configure the
  `HTTPExporter` → the ledger (`docs/integration-guide.md`). Set the redaction
  HMAC key (`AGENT_CAPTURE_HMAC_KEY`) — see key-management.
- **Reporter:** `pip install agent-capture-reporter`; runs as a CLI/library that
  reads the ledger (`agent-capture-report …`). See `packages/reporter/README.md`.

## Next
Once both services are up: review **`docs/backup-dr.md`** (you are not done until
a restore drill passes) and **`SECURITY.md`** (TLS + disclosure). Orchestration
(K8s/Helm), image publishing, and PyPI/npm release are P1 in
`docs/production-hardening-plan.md`.
