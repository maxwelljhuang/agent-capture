# agent-capture-ledger

Layer 2 of the agent-capture compliance stack: the **vendor-cloud ledger**
(in-tenant deployment optional). Receives spans from the recorder, verifies
the hash chain on write, stores
them append-only in Postgres, and periodically anchors signed Merkle roots
so trajectories are tamper-evident even against a malicious DBA.

This package runs in the **end customer's tenant**, not in the agent
vendor's cloud. The compliance vendor (us) never sees customer trajectories.

## Quick start (local dev)

```bash
cd packages/ledger
docker compose -f docker/docker-compose.yml up -d
docker compose exec ledger ledger db migrate
docker compose exec ledger ledger token create --role=ingest --customer=demo-co
# → prints a bearer token; copy it.
```

Point a recorder's `HTTPExporter` at `http://localhost:8443/spans` with
that bearer token and spans flow.

## Environment reference

| Var | Default | Notes |
| --- | --- | --- |
| `LEDGER_DATABASE_URL` | `postgresql+asyncpg://ledger_app:ledger_app@localhost:5432/ledger` | App role; INSERT+SELECT only. |
| `LEDGER_DATABASE_URL_RETENTION` | — | Retention role; the only role that can DELETE. |
| `LEDGER_LISTEN_HOST` / `LEDGER_LISTEN_PORT` | `0.0.0.0:8443` | |
| `LEDGER_SIGNING_KEY_PATH` | — | Ed25519 PEM for attestations. |
| `LEDGER_ATTESTATION_INTERVAL_SECONDS` | `300` | Window length. |
| `LEDGER_RETENTION_*_DAYS` | `7 / 90 / 2555` | TRANSIENT / STANDARD / EXTENDED. |
| `LEDGER_LOG_LEVEL` | `info` | |

## Role model

| Role | DB grants | API role |
| --- | --- | --- |
| `ledger_app` | INSERT, SELECT on spans | serves `POST /spans`, all reads |
| `ledger_reader` | SELECT on spans, INSERT on access_log | (alt; same API path) |
| `ledger_retention` | INSERT, SELECT, UPDATE, DELETE on spans | retention worker only |
| `ledger_attestation` | SELECT on spans, INSERT on attestations | anchor worker only |

The `spans` table has a trigger that raises on UPDATE/DELETE unless the
current role is `ledger_retention`. This is defense-in-depth against
application bugs and rogue admins.

## Tamper-evidence

Per-span SHA-256 + parent-pointer hash chain is enforced at ingest.
Periodic signed Merkle roots over closed trajectories are emitted by the
anchor worker; the signed attestations are exported to a configurable
external sink (file/S3/webhook). The combination means: even if an admin
silently rewrites `spans.body`, the recomputed trajectory root won't match
the leaf in the archived attestation, and the divergence is detectable.

See `docs/architecture.md` §14 (or the plan at
`.claude/plans/foamy-napping-bee.md`) for the full design.
