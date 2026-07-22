# Production hardening plan — vendor-cloud deployment

> Builds on the point-in-time audit `docs/ops-readiness.md`, updated for what
> changed since: **enforcement (L5) was added with much weaker ops than the
> ledger**, and the **dashboard was de-scoped to a separate repo** (PR #13).
> This plan covers making *this* repo's deliverables deployable and operable in
> the **vendor's cloud**. The dashboard repo has its own hardening.

## Scope — what we're hardening

This repo ships **two shapes** of artifact:

| Deliverable | Shape | Runs where | Ops maturity today |
| --- | --- | --- | --- |
| **Ledger** (`packages/ledger`) | service: FastAPI API + retention/attestation **workers** + Postgres | vendor cloud | **High** — Dockerfile, compose, `initdb.sql`, Alembic migrations, env config, `/health` `/ready` `/metrics`, structlog |
| **Enforcement** (`packages/enforcement`) | service: verdict **API** + timeout **worker** + holds Postgres | vendor cloud | **Low** — **no Dockerfile, no Alembic (uses `create_all`), no compose**; has config + `/health` + metrics |
| **Recorder SDK** (`packages/python`, `packages/typescript`) | library | inside the vendor's agent | needs **distribution** (PyPI / npm) — the product's whole pitch is `pip install agent-capture` |
| **Reporter** (`packages/reporter`) | library + CLI | vendor cloud (reads ledger) | needs **distribution** (PyPI) |

**Repo-wide gaps:** no `SECURITY.md`, `CHANGELOG.md`, `.dockerignore`,
`.env.example`; no release/publish, Dependabot, or image-scanning workflows; the
ledger CI job omits `ruff format --check` (drift can recur); CI actions are on
the deprecated Node 20.

## Severity bar
**P0** = a vendor cannot stand it up / operate it safely without this · **P1** =
needed for steady-state operations · **P2** = scale/maturity.

---

## P0 — "a design partner can deploy both services safely"

1. **Enforcement ops parity with the ledger** *(biggest functional gap — the
   service literally can't be deployed reproducibly today)*:
   - **Alembic migrations** replacing `init_db()/create_all` for the holds DB
     (mirror `packages/ledger/.../storage/migrations` + `alembic.ini`).
   - **Dockerfile** (multi-stage, non-root, tini, healthcheck) mirroring the
     ledger's; entrypoints for `enforcement serve` and `enforcement timeout-worker`.
   - **docker-compose** (db + verdict service + timeout worker) for dev/VM deploy.
   - prod config doc for `ENFORCEMENT_*` (rules path, DB, tokens, timeouts).
2. **Deployment runbook** (`docs/deployment.md`) covering **both** services end to
   end: provision Postgres → create roles → generate keys → run migrations → mint
   tokens → configure TLS → start API + workers → verify (`/ready`, `POST /verify`,
   a verdict round-trip).
3. **Key & secret management guide**: generate the ledger **Ed25519** signing
   keypair + rotation; the recorder **HMAC redaction key** (BYOK/KMS); enforcement
   service/reviewer tokens; where secrets live (K8s Secret / vault / file perms).
4. **TLS / transport**: both services serve **plaintext** today. Document a
   TLS-terminating ingress as the supported pattern (or wire `LEDGER_TLS_*` +ship
   certs). Add **`SECURITY.md`** (disclosure, supported versions, transport).
5. **Backup / restore / DR**: pg_dump/PITR + a restore **drill** for the ledger
   (the tamper-evident system of record) *and* the enforcement holds DB; document
   that the attestation chain re-verifies after restore.
6. **`.env.example` + Kubernetes Secret templates** for both services (the config
   layer already expects these).

## P1 — "operate it in steady state"

7. **Image build + publish in CI** for both service images (+ `.dockerignore`;
   fix the broken `docker-compose.demo.yml` `Dockerfile.crew` ref). Tag + push to
   a registry on release.
8. **Orchestration**: K8s manifests or a **Helm chart** per service — ledger
   (api + retention + attestation workers) and enforcement (verdict + timeout
   worker), with Secrets/ConfigMaps, probes, resource limits, replicas.
9. **Release & distribution** *(P0-adjacent for the SDK)*: publish workflow to
   **PyPI** (`agent-capture`, `agent-capture-reporter`, `agent-capture-ledger`,
   `agent-capture-enforcement`) + **npm** (`@agent-capture/sdk`); `CHANGELOG.md`;
   a `vX.Y.Z` tag convention. Adoption depends on a real `pip install`.
10. **Observability maturity**: error-code catalogs (ledger `LExxx`, recorder
    `ACxxx`, enforcement `AC5xx`); Prometheus **alert rules** (quarantine > 0,
    inflight near high-watermark, attestation/verdict-service failures, holds
    backlog); Grafana dashboards; an SLO/SLI doc.
11. **Supply-chain security**: Dependabot/renovate; `pip-audit`/`bandit`; Trivy
    image scanning in CI. (A bank's third-party-risk review will require these.)
12. **CI hygiene**: add `ruff format --check` to the **ledger** job (closes the
    known gap); bump the Node-20 actions (`checkout`, `setup-uv`, `setup-node`,
    `pnpm/action-setup`) before they're forced off ~2026-09.

## P2 — scale / maturity
13. HA / read-replica / failover guidance; capacity planning (pool sizes, worker
    cadence, retention windows); multi-tenant deployment patterns; load/soak
    testing beyond the per-span micro-budgets in `tests/perf/`.

---

## Phased sequence

- **Phase A — make both services deployable (P0 #1–6).** Start with **enforcement
  ops parity (#1)** — it's a *code* gap (no Dockerfile/migrations), not just docs,
  and blocks any reproducible deploy — then the runbook + keys + TLS/SECURITY.md +
  backup/DR + env/Secret templates. *Exit:* a partner stands up ledger +
  enforcement on a VM from the runbook, with TLS, backups, and verified
  attestations.
- **Phase B — operate + distribute (P1 #7–12).** Images+publish, Helm, PyPI/npm
  release, alerts/dashboards/error-catalogs, supply-chain scanning, CI hygiene.
  *Exit:* `pip install agent-capture` works; images are published + scanned; a
  release is one tagged, automated step; ops can see + alert on the services.
- **Phase C — scale (P2).** HA, capacity, multi-tenant, load testing.

## Recommended first PR
**Enforcement ops parity (P0 #1)** — Alembic migration + Dockerfile + compose +
prod config for the enforcement service. It converts enforcement from
"runs on my machine via `create_all`" to "deployable like the ledger," and is
the prerequisite for the runbook, images, and Helm. (The ledger is already most
of the way there, so the doc bundle — runbook/keys/TLS/DR — is the natural
second PR.)

## Verification (per item)
- **Deployability:** a clean VM + the runbook brings up ledger + enforcement;
  `/ready` green on both; an ingested trajectory is queryable; a verdict
  round-trips; a held action resolves.
- **Migrations:** `ledger db migrate` and the new `enforcement db migrate` both
  reach head on an empty Postgres; no `create_all` in the prod path.
- **DR:** backup → drop → restore → `POST /verify` re-verifies the chain; holds
  survive.
- **Distribution:** the published wheels/sdist install in a clean venv and import;
  the npm package resolves; CI image scan + `pip-audit` are green.
- **CI:** ledger `ruff format --check` passes; no Node-20 deprecation warnings.

## Open decisions
- D1. **Orchestration target** — K8s/Helm vs. plain compose-on-VM as the
  *supported* deploy (drives #8's effort). Recommend Helm + a documented compose
  fallback.
- D2. **Registry + PyPI/npm ownership** — which org/namespaces publish the images
  and packages; who holds the signing/publish credentials.
- D3. **TLS posture** — ingress-terminated (recommended) vs. in-process certs via
  `LEDGER_TLS_*`/new `ENFORCEMENT_TLS_*`.
- D4. **Single vs. per-service Postgres** — ledger and enforcement each default to
  their own DB; confirm that's the deployment shape (vs. shared instance, separate
  schemas).
