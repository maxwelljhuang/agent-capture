# Loan-denial toy agent

The canonical end-to-end demo. Running `run.py` produces a trajectory
that exercises every layer of the SDK and satisfies every assertion in
[`docs/reporting-fields.md`](../../../../docs/reporting-fields.md).

## Run

```bash
AGENT_CAPTURE_HMAC_KEY=demo-key \
  PYTHONPATH=packages/python/src \
  python packages/python/examples/loan_denial/run.py
```

Outputs `trajectory.jsonl` in the working directory.

## Verify

```bash
PYTHONPATH=packages/python/src python scripts/verify_trajectory.py trajectory.jsonl
```

Exits 0 if every report-generator contract is satisfied (universal
per-span fields; one root + linked hash chain; ECOA adverse-action
readiness; SR 11-7 model documentation; BSA/AML SAR-supporting
evidence).

## What's exercised

| Layer | How |
| --- | --- |
| All 8 span types | model_call, tool_call, retrieval, planner_step, sub_agent_invocation, human_approval, side_effect, policy_check |
| Manual decorator | `@traced(type=..., name=..., attributes=...)` on every step function |
| Context manager | `with traced(...):` for the root planner_step and the inner `decide` |
| SDK wrapper | `wrap(client).messages.create(...)` auto-emits a `model_call` |
| Redaction policy | `policy.yaml` loaded by `load_policy()`; SSN → full, account/routing → HMAC |
| Exporter pipeline | `BoundedQueueExporter` wrapping `FileExporter` so the hot path never blocks |

The trajectory shape matches the scenario pinned by
`tests/scenarios/test_loan_approval.py`.
