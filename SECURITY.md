# Security policy

`agent-capture` is compliance infrastructure for AI agents that make legally
consequential decisions. We take security and data-custody seriously.

## Reporting a vulnerability
Email **security@agent-capture.dev** (replace with your real contact) with
details and a PoC if possible. Please do **not** open public issues for
vulnerabilities. We aim to acknowledge within 2 business days and to ship a fix
or mitigation for confirmed high-severity issues promptly.

## Supported versions
Pre-1.0: the latest minor release receives security fixes. Pin a version in
production and watch releases. (A formal support window lands with the first
tagged release — see `docs/production-hardening-plan.md`.)

## Transport security (TLS)
The ledger and enforcement services **bind plaintext** and are designed to run
behind a **TLS-terminating ingress** — terminate TLS there and restrict the
plaintext bind to the pod network. (The ledger also exposes `LEDGER_TLS_CERT_PATH`
/ `LEDGER_TLS_KEY_PATH` for in-process termination if you prefer.) Do **not**
expose either service's plaintext port to an untrusted network. The recorder
ships spans to the ledger over **HTTPS**.

## Data custody (the organizing principle)
- Regulated trajectory data stays in the **vendor's cloud** (recorder + ledger +
  reporting + enforcement). The dashboard (a separate repo, our cloud) queries
  the ledger's query API over the wire and persists no regulated data.
- **Redaction runs in-process** in the recorder before any span leaves the
  agent's memory, so the durable record (the ledger) never holds raw PII.
- Ledger reads are **tenant-scoped** (reader tokens see only their
  `end_customer_id`) and **every read is access-logged**.
- Provenance is cryptographic: per-span `content_hash` chains + periodic
  Ed25519-signed Merkle attestations a regulator can verify independently.

## Secrets
API tokens are Argon2-hashed and shown once. The Ed25519 signing key and the
recorder HMAC key are customer-/vendor-held secrets — see
**`docs/key-management.md`**. Never commit secrets; use a KMS / Kubernetes Secret
(templates in `deploy/k8s/`).

## Hardening status
Production-hardening is tracked in `docs/production-hardening-plan.md`
(deployment runbook, key management, backup/DR, supply-chain scanning, release).
