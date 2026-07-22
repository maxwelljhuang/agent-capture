# agent-capture

Compliance infrastructure for AI agents that make legally consequential
decisions (loan underwriting, KYC, fraud review). An SDK captures an agent's
full decision **trajectory**; vendor-cloud services then store it
tamper-evidently and turn it into regulatory artifacts.

See [`docs/architecture.md`](docs/architecture.md) for the full design.

## What's here

This repo is the **vendor-cloud stack** — the SDK a vendor installs in their
agent, plus the services that run in the vendor's cloud over the vendor's data:

| Layer | Package | Role |
| --- | --- | --- |
| **Recorder** (L1, incl. redaction) | `packages/python`, `packages/typescript` | Capture the trajectory in-process; redact, then ship |
| **Ledger** (L2) | `packages/ledger` | Append-only Postgres store, hash-chain + Ed25519 Merkle attestations, retention, role-scoped read API |
| **Reporting** (L3) | `packages/reporter` | Render ECOA Adverse-Action + SR 11-7 notices from the ledger |
| **Enforcement** (L5) | `packages/enforcement` | Gate `side_effect` / `human_approval` actions; advisory → hold → block |

The **dashboard / query layer ("Kelp") is a separate repo** in our cloud; it
queries this repo's ledger API over the wire. It is **not** built here.

## Start here

- **Testing it?** → [`docs/tester-quickstart.md`](docs/tester-quickstart.md) —
  clone → captured, stored, queried, reported-on trajectory in ~15 min, from
  source.
- **Integrating the SDK?** → [`docs/integration-guide.md`](docs/integration-guide.md)
  (§0 lists what a vendor must host & provide).
- **Deploying the services?** → [`docs/deployment.md`](docs/deployment.md),
  [`docs/key-management.md`](docs/key-management.md),
  [`docs/backup-dr.md`](docs/backup-dr.md).
- **Querying the ledger (dashboard / control plane)?** →
  [`docs/ledger-api-contract.md`](docs/ledger-api-contract.md) — the versioned
  read/enforcement API contract.

## Develop

```bash
uv sync --all-packages --group dev   # whole Python workspace
uv run pytest packages/python        # recorder tests (per-package)

pnpm install && pnpm -r test         # TypeScript SDK
```

The ledger and enforcement tests need Postgres; their
`docker/docker-compose.yml` files bring one up.

## Schema is the source of truth

The Pydantic models in `packages/python/src/agent_capture/schema/` generate
`schemas/span.schema.json`, which generates the TypeScript types. Never edit
the generated artifacts by hand. After changing a model:

```bash
uv run python scripts/generate_schema.py
./scripts/generate_ts_types.sh
```

CI fails if a committed artifact drifts from the generated one.
