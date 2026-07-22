# Key & secret management

The stack has three classes of secret. None may live in code, committed env
files, or logs. In production hold them in a **KMS / Kubernetes Secret / vault**;
on a VM, files with `0600` perms owned by the service user.

## 1. Ledger attestation signing key (Ed25519)

The ledger signs periodic Merkle attestations with an Ed25519 key
(`integrity/signer.py:FileEd25519Signer` reads an **unencrypted PKCS8 PEM**).

**Generate:**
```bash
# openssl (PKCS8, unencrypted — what the signer loads)
openssl genpkey -algorithm ED25519 -out primary.priv.pem
openssl pkey -in primary.priv.pem -pubout -out primary.pub.pem
chmod 600 primary.priv.pem
```
(Equivalent: the bundled `agent_capture_ledger.integrity.signer.generate_keypair(out_dir, key_id)`.)

**Wire:** `LEDGER_SIGNING_KEY_PATH=/secrets/primary.priv.pem`,
`LEDGER_SIGNING_KEY_ID=primary`. **Publish `primary.pub.pem`** to the
bank/regulator — it's how they independently verify attestation signatures.

**Rotate:** generate a new key with a *new* `key_id` (e.g. `primary-2026q3`),
switch `LEDGER_SIGNING_KEY_ID`, and **keep every old public key** — historical
attestations were signed by the key live at the time, so old pubs are needed to
verify them forever. Never reuse a `key_id` for a new key.

## 2. Recorder redaction HMAC key (customer-managed / BYOK)

The recorder's HMAC fingerprint strategy keys off `AGENT_CAPTURE_HMAC_KEY`
(looked up lazily per redaction). It is **customer-managed (BYOK via KMS)**;
the key is injected into the vendor-side recorder at use time and never
persisted to the durable record.

- **Generate:** any ≥32-byte high-entropy secret (`openssl rand -base64 48`).
- **Rotate:** the lazy lookup means a new value takes effect without restart —
  but fingerprints are deterministic over the key, so values fingerprinted under
  the old key won't re-map under the new one. Rotate on a documented boundary and
  record which key-epoch covers which period.

## 3. API tokens (ledger + enforcement)

- **Ledger** — Argon2-hashed, minted by `ledger token create --role … --customer …`,
  printed **once** as `<token_id>.<secret>`. Revoke via `ledger token revoke`.
  Roles: `ingest` (recorder→ledger), `reader` (dashboard/reporter), `admin`
  (cross-tenant ops).
  - **Unscoped ingest token** (`ledger token create --role ingest --unscoped`):
    a single first-party token that may ingest spans for **any** `end_customer_id`
    — for a multi-tenant SaaS process that stamps the tenant per span (#63). It
    widens ingest-side trust (a holder can attribute spans to any tenant), so
    treat it like the service's primary secret: first-party only, never hand to a
    third party, rotate on a schedule. Reads stay tenant-scoped regardless.
- **Enforcement** — `ENFORCEMENT_SERVICE_TOKEN` (the recorder client + hold
  resolution polling) and `ENFORCEMENT_REVIEWER_TOKEN` (the `/holds` review API).
  Generate as high-entropy secrets; rotate by setting the new value and updating
  the recorder client + reviewers.

## Storage rules (all three)
- KMS/Secret/vault in prod; `0600` files owned by the service user on a VM.
- Mount as env or a file the process reads — never bake into images or commit.
- The Dockerfiles run as a non-root user (uid 10001); ensure mounted secret files
  are readable by it.
- See `deploy/k8s/*-secret.example.yaml` for Secret templates and
  `packages/*/.env.example` for the full variable lists.
