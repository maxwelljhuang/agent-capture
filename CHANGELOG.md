# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Distributed artifacts (see `docs/releasing.md`):
- **PyPI** — `agent-capture` (recorder SDK), `agent-capture-reporter`,
  `agent-capture-ledger`, `agent-capture-enforcement`.
- **npm** — `@agent-capture/sdk` (TypeScript recorder).

## [Unreleased]

## [0.1.0] - 2026-06-04

First release of the vendor-cloud compliance stack.

### Added
- **Recorder SDK** (`agent-capture`, `@agent-capture/sdk`): trajectory capture
  via `@traced`, SDK wrappers (Anthropic/OpenAI), LangGraph + CrewAI adapters,
  W3C context propagation, in-process redaction (full + HMAC), and a bounded
  async exporter (file / HTTP / OTel) that never blocks the host.
- **Ledger** (`agent-capture-ledger`): append-only partitioned Postgres store,
  ingest hash-chain verification, Ed25519-signed Merkle attestations with
  inclusion proofs, retention enforcement, role-scoped reads, and a query API
  (`/trajectories`, `/trajectories/{id}/spans`, `/stats`, `/verify`,
  `/attestations`).
- **Reporter** (`agent-capture-reporter`): ECOA Adverse Action Notice and
  SR 11-7 Model Inventory renderers (HTML/PDF + provenance manifest).
- **Enforcement** (`agent-capture-enforcement`): recorder-side gate hook, inline
  verdict service, fail-to-human hold queue + review API, and advisory-at-ingest;
  selective gating of `side_effect` / `human_approval`; tiered failure modes.
- **Deployment**: Dockerfiles + docker-compose + Alembic migrations for the
  ledger and enforcement services; deployment runbook, key-management, and
  backup/DR docs; `.env.example` + Kubernetes Secret templates; `SECURITY.md`.
- **Release pipeline**: tag-triggered build + publish to PyPI (Trusted
  Publishing) and npm; manual dry-run / TestPyPI dispatch.

[Unreleased]: https://github.com/maxwelljhuang/agent-capture/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/maxwelljhuang/agent-capture/releases/tag/v0.1.0
