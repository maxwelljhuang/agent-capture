# agent-capture-reporter

Layer 3 of the agent-capture compliance stack: the **renderer** that turns
captured agent trajectories into compliance-officer-facing documents. Each
renderer lives in its own subpackage (`ecoa/`, `sr_11_7/`) over a shared
`common/` layer (corpus loading, manifest base, hash-chain verification).

Two renderers ship today:

- **ECOA Adverse Action Notice** (`ecoa/`, Regulation B §1002.9 + FCRA §615) —
  one trajectory → one consumer-facing notice.
- **SR 11-7 Model Inventory** (`sr_11_7/`, Federal Reserve model risk guidance) —
  a *corpus* of trajectories over a reporting period, joined to a customer model
  governance registry → one aggregate inventory of every model in use.

Both emit **HTML** + **PDF** plus a JSON **manifest** that traces document
content back to source span `content_hash`es.

## Quick start

```bash
# ECOA Adverse Action Notice (one trajectory):
uv run --package agent-capture-reporter agent-capture-report adverse-action decision.jsonl -o out/ --no-pdf

# SR 11-7 Model Inventory (a corpus + a governance registry):
uv run --package agent-capture-reporter agent-capture-report model-inventory ./corpus/ \
    --registry registry.yaml --period 2026-01-01:2026-03-31 -o out/ --no-pdf

# Drop --no-pdf to also render PDF (requires the 'pdf' extra + cairo/pango/gdk-pixbuf).
```

ECOA produces `out/notice.{html,pdf}` + `out/manifest.json`; the inventory
produces `out/inventory.{html,pdf}` + `out/manifest.json`.

## The model governance registry (SR 11-7)

SR 11-7 inventory columns the recorder does **not** capture — intended use, risk
tier, validation status, limitations, monitoring — come from a customer-owned
registry (YAML or JSON), joined to observed usage on `model_card_version`
(falling back to the `(provider, model_name, model_version)` identity). Like the
redaction policy and the ECOA reason taxonomy, the registry is the customer's,
not the vendor's. Governance cells sourced from it are **outside** the span hash
chain, and the manifest labels them as such (`provenance_kind:
governance_registry`).

```yaml
models:
  - model_card_version: claude-opus-4-7.lending.v3
    provider: anthropic
    model_name: claude-opus-4-7
    intended_use: Unsecured personal loan underwriting risk score.
    risk_tier: tier_1
    validation_status: validated
    last_validated: 2026-02-01
    valid_until: 2027-02-01
    limitations: Not for commercial lending.
    monitoring: owner=mrm@acme-bank; cadence=quarterly; status=active
```

## Library use

```python
from datetime import UTC, datetime
from agent_capture_reporter import load_trajectory, render_adverse_action

trajectory = load_trajectory("decision.jsonl")
rendered = render_adverse_action(trajectory, generated_at=datetime.now(UTC), with_pdf=False)
print(rendered.manifest.completeness_score)
```

## The manifest is the contract

**ECOA** — every rendered section names the spans that fed it
(`source_content_hashes`), so an auditor can pull those exact spans from the
ledger and re-verify. Legally-**required** content (decision; principal reasons)
raises `IncompleteTrajectoryError` rather than producing a deficient notice;
expected-but-optional content (CRA, human review, delivery proof) renders a
`[NOT CAPTURED]` marker and records an `expected` gap.

**SR 11-7** — one inventory row can derive from hundreds of trajectories, so per
row the manifest carries the full `contributing_trajectory_ids` list plus an
`evidence_digest` (a recomputable SHA-256 set commitment over the contributing
`model_call` content hashes) and a bounded `sample_citations` set (with a
`citations_truncated` flag — caps are never silent). The gap posture is
**inverted** vs ECOA: the inventory exists to catalogue deficiencies, so
ungoverned / unvalidated models are *rendered as findings* (with `NO GOVERNANCE
CARD` / `[NOT IN REGISTRY]` markers), never hidden. Only "no inventory can be
formed" conditions (empty corpus, no in-period usage, ambiguous multi-tenant
corpus) raise `IncompleteInventoryError`.

Across both, `gaps[]` is always populated (empty when complete), and the
manifest binds to the exact rendered bytes via `html_sha256` / `pdf_sha256`.

The contract for which span fields feed which document is `docs/reporting-fields.md`,
enforced end-to-end by `scripts/verify_trajectory.py`. (Note: the SR 11-7
inventory's registry-sourced columns are not yet documented there — a follow-up
`docs/sr-11-7-spec.md` is the right home.)

## Out of scope for v1

Per-model performance metrics (drift/accuracy/fair-lending), validation-doc
generation, model-card authoring, other notice types, localization, delivery
(email/print), e-signature, and TypeScript parity.
