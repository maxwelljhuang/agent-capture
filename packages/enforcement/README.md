# agent-capture-enforcement

Layer 5 of the agent-capture compliance stack: the **enforcement engine**
(vendor cloud). It evaluates bank-authored compliance rules against an agent's
trajectory and can act on the two *gated* span types — `side_effect` and
`human_approval` — while everything else is advisory only.

See `docs/enforcement-plan.md` at the repo root for the full design. The
recorder-side gate hook (the contract this engine implements) lives in
`agent_capture.enforcement` in the `agent-capture` package.

## Deploy

Two processes share one image (`packages/enforcement/docker/Dockerfile`): the
**verdict service** (`enforcement serve`, port 8475, `/health`) and the
**hold-timeout worker** (`enforcement timeout-worker`). The hold queue lives in
Postgres; the production schema path is Alembic:

```bash
ENFORCEMENT_DATABASE_URL=postgresql+asyncpg://… enforcement db migrate   # upgrade head
```

`enforcement db init` (create_all) exists for dev/tests only. `docker compose -f
packages/enforcement/docker/docker-compose.yml up` brings up db + service +
worker (migrating on start). Config is env-driven (`ENFORCEMENT_*`): rules path,
DB URL, service/reviewer tokens, timeouts.

## What's here

- **Rule model + loader** (`rules.py`) — bank-authored, versioned YAML, loaded
  vendor-side. Mirrors the redaction policy loader.
- **Evaluators** (`evaluator.py`) — named, immediate-span predicates over a
  span's typed attributes + compliance metadata.
- **Verdicts** (`verdict.py`) — the outcome of evaluating a rule; maps onto the
  existing `policy_check` span schema (no schema change).
- **Advisory** (`advisory.py`) — non-blocking evaluation used at the ledger
  ingest boundary: produce `policy_check` verdicts + alert, never block.

Later phases add the inline verdict service, the fail-to-human hold queue, and
fail-closed blocking (see the plan).
