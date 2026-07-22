# Ops / deployment readiness audit

**Date:** 2026-06-02 · **Type:** read-only audit (point-in-time) · **Bar:** "could a
regulated bank deploy and operate this in production?"

This audits *deploy/operate* readiness, not capture-time performance (that's
covered by `packages/python/tests/perf/`). Each finding cites the file that
evidences it (or its absence). Nothing here changes a running system — the report
is the only artifact.

**Headline:** the **ledger is much closer to deployable than expected** — it
ships a hardened container, env-driven config, DB migrations + roles, health
probes, Prometheus metrics, and structured logging. The gaps are not "build the
basics," they are: **a repeatable, documented, secure deploy** (runbook, key
generation, secret storage, TLS, backup/DR), production **orchestration**
(K8s/Helm), **release/distribution** automation, and **security/ops maturity**
(scanning, alerts, dashboards, error catalog).

---

## Scorecard

| # | Dimension | Status | Worst gap | Severity |
| --- | --- | --- | --- | --- |
| 1 | Packaging & image build | 🟡 | no image publish; no `.dockerignore`; broken demo Dockerfile ref | P1 |
| 2 | Deployment & orchestration | 🟡 | dev compose only; no K8s/Helm/IaC; **no deploy runbook** | P0 |
| 3 | Configuration | 🟡 | no `.env.example` / Secret template; recorder SDK not env-driven | P1 |
| 4 | Secrets & key management | 🟡 | no Ed25519 keygen / rotation / secret-storage guidance | P0 |
| 5 | Database lifecycle | 🟡 | **no backup/restore / DR**; no HA; one migration only | P0 |
| 6 | Network / TLS posture | 🟡 | shipped stack runs **plaintext**; TLS knobs unused; no `SECURITY.md` | P0 |
| 7 | Observability | 🟡 | no error-code catalog, alert rules, or dashboards | P1 |
| 8 | Release & distribution | 🟡 | no publish/release workflow; no `CHANGELOG`; no tags | P1 |
| 9 | Supply-chain security | ❌ | no dependency/SAST/image scanning; no Dependabot | P1 |
| 10 | Operator documentation | 🟡 | local quick-start only; no prod runbook/troubleshooting/DR | P0 |

✅ ready · 🟡 partial · ❌ missing. **P0** = blocks a safe Day-1 deploy · **P1** =
needed for steady-state operations · **P2** = maturity/polish.

---

## Findings by dimension

### 1. Packaging & image build — 🟡
- **Have:** `packages/ledger/docker/Dockerfile` — multi-stage, runs as non-root
  `ledger` (UID 10001), `tini` init, `HEALTHCHECK` on `/health`, `ENTRYPOINT
  ["tini","--","ledger"]`. Solid.
- **Gaps:** no `.dockerignore` (whole repo context is sent to the build);
  `docker-compose.demo.yml` references a `Dockerfile.crew` that **does not exist**
  (the demo is broken); the image is **never built or published** by CI; image is
  built by `pip install` from source rather than a pinned wheel. Recorder/reporter
  are libraries (no container needed) — correct.
- **Severity P1** (build hygiene + repeatable images), except the broken demo ref
  is a quick **P2** fix.

### 2. Deployment & orchestration — 🟡 → **P0**
- **Have:** `packages/ledger/docker/docker-compose.yml` — db + ledger, role-scoped
  DSNs (`ledger_app`, `ledger_retention`, migrate via superuser), `depends_on`
  health gate, named volume. Good for **dev**.
- **Gaps:** no Kubernetes manifests, Helm chart, or IaC; no `deploy/`/`infra/`
  dir; compose has no restart policy, resource limits, or replicas. Banks deploy
  on orchestrated infra — there's nothing to hand them.
- **Severity P0** for the **runbook** (you can deploy the existing compose on a VM
  *if* someone documents how); K8s/Helm is **P1** (scale-up).

### 3. Configuration — 🟡
- **Have:** `config.py` (pydantic-settings, `env_prefix="LEDGER_"`, frozen) — every
  knob documented inline; README has an env table. Its docstring even says prod
  "mount[s] a single env file or a Kubernetes Secret."
- **Gaps:** …but **no `.env.example` and no Secret template** are shipped, so that
  intended path isn't paved. The **recorder SDK** is configured programmatically
  via `configure()` only (`packages/python/src/agent_capture/config.py`) — no
  env-driven production path, and `docker-compose.demo.yml` sets `AGENT_CAPTURE_*`
  vars the SDK doesn't read.
- **Severity P1.**

### 4. Secrets & key management — 🟡 → **P0**
- **Have:** HMAC redaction key via `AGENT_CAPTURE_HMAC_KEY` (lazy read, supports
  rotation); ledger Ed25519 signing key via `LEDGER_SIGNING_KEY_PATH` +
  `integrity/signer.py:FileEd25519Signer`; API tokens minted by `ledger token
  create` (Argon2-hashed, shown once).
- **Gaps:** no documented way to **generate** the Ed25519 keypair, no **rotation**
  procedure (signing key or HMAC key), and no guidance on **where secrets live**
  (K8s Secret, vault, file perms/ownership). A deployer literally cannot start
  attestations without being told how to make the key.
- **Severity P0** (you can't operate the tamper-evidence feature without it, and
  fumbled key handling is a security incident).

### 5. Database lifecycle — 🟡 → **P0**
- **Have:** Alembic + `ledger db migrate|init|current|downgrade`
  (`cli/db.py`); roles created by `initdb.sql` / `ledger db init`; append-only
  enforced by a DB trigger gated to `ledger_retention`. Strong.
- **Gaps:** **no backup/restore or PITR guidance** for the system of record — for a
  tamper-evident compliance ledger this is the most glaring omission; no HA /
  failover / read-replica guidance; only one migration exists, with no documented
  forward-migration discipline.
- **Severity P0** (a bank's own auditors require a backup/DR story for the record
  of record).

### 6. Network / TLS posture — 🟡 → **P0**
- **Have:** TLS config knobs `LEDGER_TLS_CERT_PATH` / `LEDGER_TLS_KEY_PATH` exist
  in `config.py`; role separation + Argon2 tokens.
- **Gaps:** the shipped Dockerfile healthcheck and `docker-compose.yml` run
  **plaintext HTTP on 8443** — the TLS knobs are never set, so out-of-the-box the
  ledger serves unencrypted. No `SECURITY.md`, no documented TLS-terminating-ingress
  pattern, no network-policy guidance. Spans contain redacted-but-sensitive
  compliance data; in-tenant or not, plaintext transport is unacceptable for a bank.
- **Severity P0** (either document TLS termination at an ingress, or wire the
  TLS knobs and ship certs — but "plaintext by default, undocumented" can't ship).

### 7. Observability — 🟡
- **Have:** `/health`, `/ready` (checks DB + schema), `/version`, `/metrics`;
  9 Prometheus metrics (`observability/metrics.py`: ingest count/latency/batch,
  chain-verification failures, inflight gauge, quarantine size, attestation +
  retention counters); structlog JSON to stdout (`observability/logging.py`).
- **Gaps:** the API error codes (`LExxx`) have **no published catalog**; no
  Prometheus **alert rules** (e.g. quarantine > 0, inflight near high-watermark,
  attestation export failures); no **Grafana dashboard**; no SLO/SLI doc.
- **Severity P1.**

### 8. Release & distribution — 🟡
- **Have:** all three Python packages are hatchling/PyPI-ready (name, version,
  classifiers, entry points `ledger` and `agent-capture-report`); TS
  `@agent-capture/sdk` is npm-ready with a build script.
- **Gaps:** no publish/release workflow (no `.github/workflows/release.yml` or
  `publish.yml`), no `CHANGELOG.md`, no git-tag convention (zero tags), no
  container-registry publish. The recorder SDK's whole value proposition is
  "vendor `pip install agent-capture`" — which requires actually publishing it.
- **Severity P1** (P0-adjacent for the recorder SDK specifically, since adoption
  depends on a real PyPI release).

### 9. Supply-chain security — ❌
- **Gaps:** no Dependabot/renovate, no SAST (bandit/Semgrep), no dependency
  audit, no container image scanning (Trivy/Grype) in CI. A bank's third-party
  risk review will ask for all of these.
- **Severity P1.**

### 10. Operator documentation — 🟡 → **P0**
- **Have:** per-package READMEs; `packages/ledger/README.md` has a **local**
  docker-compose quick-start + env table + role table; `docs/architecture.md` +
  `docs/integration-guide.md` (vendor SDK install).
- **Gaps:** no **production deployment runbook** (provision DB → create roles →
  generate signing key → run migrations → mint tokens → configure TLS → start →
  verify), no troubleshooting guide, no capacity planning, no DR procedure, no
  multi-tenant deployment guidance.
- **Severity P0** (the runbook is the difference between "demoable" and
  "a partner can stand it up themselves").

---

## Remediation backlog

### P0 — "a design partner can deploy it safely" (do first)
1. **Production deployment runbook** (`docs/deployment.md`): end-to-end —
   provision Postgres, `ledger db init`, generate the Ed25519 key, set roles/DSNs,
   mint tokens, configure TLS, start, verify via `/ready` + `POST /verify`.
2. **Key & secret guide**: Ed25519 keypair generation command, rotation procedure
   (signing + HMAC), and secret-storage guidance (file perms, K8s Secret, vault).
3. **TLS story**: either document a TLS-terminating ingress as the supported
   pattern, or wire `LEDGER_TLS_*` into `serve` + compose and ship a cert step.
   Add `SECURITY.md`.
4. **Backup / restore / DR doc**: pg_dump/PITR strategy, restore drill, and how
   restore interacts with attestations (you can re-verify the chain after restore).
5. **`.env.example` + a Kubernetes Secret template** (the config layer already
   expects these).

### P1 — "operate it in steady state"
6. K8s manifests or a Helm chart (Deployment, Service, ConfigMap, Secret,
   the retention/attestation workers).
7. Container image build + publish in CI; add `.dockerignore`.
8. Error-code catalog (`docs/error-codes.md`) + Prometheus alert rules + a Grafana
   dashboard JSON.
9. Supply-chain: Dependabot + `bandit`/`pip-audit` + Trivy image scan in CI.
10. Release/publish workflow (PyPI for the 3 Python pkgs + npm for the SDK),
    `CHANGELOG.md`, and a `vX.Y.Z` tag convention.

### P2 — maturity/polish
11. Capacity-planning + scaling guide (pool sizes, worker cadence, retention);
    multi-tenant deployment patterns; HA/read-replica; fix the demo's missing
    `Dockerfile.crew`.

---

## Recommended first step

The natural next PR is the **P0 "deployable partner" bundle (items 1–5)** — all
docs + two small templates, no service code, low risk, and it converts the ledger
from "runs on my machine via compose" to "a partner can stand it up securely and
not lose the record of record." K8s/Helm and the release pipeline (P1) follow once
a partner is actually deploying.
