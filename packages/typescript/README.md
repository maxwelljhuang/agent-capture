# @agent-capture/sdk

The TypeScript SDK for capturing AI agent trajectories. Mirrors the Python
package; see the [repository root](../../README.md) and
[architecture doc](../../docs/architecture.md) for context.

## Status

Pre-alpha. Week 1 ships generated schema types only; runtime modules are
scaffolded for Weeks 2-6.

## Install

```bash
pnpm add @agent-capture/sdk
```

## Types

```ts
import type { AgentCaptureSpan } from "@agent-capture/sdk/schema";
```

Generated from `schemas/span.schema.json` at the repo root. Run
`./scripts/generate_ts_types.sh` after the Pydantic source-of-truth changes.
