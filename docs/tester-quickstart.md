# Tester quickstart (from source — nothing published)

Get from a clone to a captured, stored, queried, and reported-on agent
trajectory in ~15 minutes — building everything from source (no PyPI, no npm, no
image registry). For the full design see `docs/architecture.md`; for the
production path see `docs/deployment.md`.

**Prerequisites:** `git`, [`uv`](https://docs.astral.sh/uv/), Docker (+ Compose),
and a terminal. Python 3.11–3.13.

```bash
git clone https://github.com/maxwelljhuang/agent-capture && cd agent-capture
uv sync --all-packages --group dev          # install the whole workspace from source
```

---

## Track A — SDK only, no services (2 minutes)

Proves the recorder works with zero infrastructure (writes a local JSONL file).

```python
# my_agent.py
from agent_capture import configure, traced
from agent_capture.exporter import FileExporter
from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.compliance import DataClassification, RegulatoryRegime, RetentionClass

configure(
    exporter=FileExporter("trajectory.jsonl"),
    default_compliance=ComplianceMetadata(
        policy_version_active="dev-v1",
        agent_version="my-agent@0.1.0",
        end_customer_id="acme-bank",
        regulatory_regime=[RegulatoryRegime.ECOA],
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    ),
)

@traced(type=SpanType.RETRIEVAL, name="fetch_credit_report")
def fetch_credit_report(applicant_id: str) -> dict:
    return {"score": 700}

@traced(type=SpanType.PLANNER_STEP, name="underwrite")
def underwrite() -> str:
    fetch_credit_report("app-9001")
    return "deny"

if __name__ == "__main__":
    print(underwrite())
```
```bash
uv run python my_agent.py
cat trajectory.jsonl | head -1 | python -m json.tool   # spans with hashes + compliance metadata
```
Richer, runnable examples: `packages/python/examples/loan_denial/` and
`packages/python/examples/crew_demo/`.

---

## Track B — full pipeline: SDK → ledger → query → report (~10 minutes)

### 1. Start the ledger (builds the image from source, migrates, serves on :8443)
```bash
docker compose -f packages/ledger/docker/docker-compose.yml up -d
curl -fsS http://localhost:8443/ready        # {"status":"ready", ...}
```

### 2. Mint API tokens (run the CLI inside the container)
```bash
LEDGER="docker compose -f packages/ledger/docker/docker-compose.yml exec -T ledger ledger"
$LEDGER token create --role ingest --customer acme-bank   # copy the printed token
$LEDGER token create --role reader --customer acme-bank   # copy this one too
```
Each token prints **once** as `<token_id>.<secret>`.

### 3. Ship spans to the ledger (swap the exporter from Track A)
```python
# my_agent.py — replace the FileExporter block with:
import os
from agent_capture import configure, traced
from agent_capture.exporter import BoundedQueueExporter, HTTPExporter
from agent_capture.schema import ComplianceMetadata, SpanType  # + compliance imports as above

ledger = HTTPExporter("http://localhost:8443/spans", auth_token=os.environ["AGENT_CAPTURE_LEDGER_TOKEN"])
configure(exporter=BoundedQueueExporter(ledger), default_compliance=ComplianceMetadata(... end_customer_id="acme-bank" ...))
# ... same @traced functions; call underwrite() ...
```
```bash
AGENT_CAPTURE_LEDGER_TOKEN="<ingest-token>" uv run python my_agent.py
```
> The span's `end_customer_id` **must match** the ingest token's `--customer`
> (`acme-bank` here), or the ledger rejects it for tenant mismatch.

### 4. Query it back (reader token)
```bash
R="<reader-token>"
curl -H "Authorization: Bearer $R" http://localhost:8443/trajectories            # list
TID="<trajectory_id from the list>"
curl -H "Authorization: Bearer $R" http://localhost:8443/trajectories/$TID/spans  # the decision graph
curl -XPOST -H "Authorization: Bearer $R" http://localhost:8443/verify/$TID       # chain_intact: true
curl -H "Authorization: Bearer $R" http://localhost:8443/stats                    # aggregate counts
```

### 5. Generate a compliance report from source
```bash
uv run --package agent-capture-reporter agent-capture-report adverse-action \
  --ledger-url http://localhost:8443 --ledger-token "<reader-token>" \
  --trajectory-id "$TID" --out ./report
ls report/   # notice.html, manifest.json (notice.pdf needs the 'pdf' extra/WeasyPrint)
```

That's the whole pipeline: **capture → store (tamper-evident) → query → report**, all from source.

---

## Track C — enforcement (optional, ~5 minutes)

The verdict service that can gate `side_effect` / `human_approval` actions.

```bash
docker compose -f packages/enforcement/docker/docker-compose.yml up -d   # :8475 (see port note below)
curl -fsS http://localhost:8475/health
```
Then in the agent, register the gate and add a `side_effect` step; with a
bank-authored rule file (`ENFORCEMENT_RULES_PATH`) a denylisted action is blocked
or held for review. See `docs/enforcement-plan.md` §9 (advisory-first) and the
`EnforcementClient` wiring in `docs/deployment.md` Part 2.

---

## Known rough edges (you'll likely hit these — please report them)

1. **Port clash:** both compose files publish Postgres on **5432**. Don't run the
   ledger and enforcement stacks at once on the same host without remapping one
   (`ports: ["5433:5432"]`), or comment out the db port publish.
2. **Token minting is a `docker exec`**, not a one-liner — clunky but it works.
3. **Attestations need a signing key** you generate + mount
   (`docs/key-management.md`). Ingest/query/report all work **without** it; only
   the periodic signed Merkle attestations are off until you set
   `LEDGER_SIGNING_KEY_PATH`.
4. **TLS is plaintext** locally (terminate at an ingress in prod — `SECURITY.md`).

## What "working" looks like (report back)
- A span JSONL (Track A) and/or trajectories listed from the ledger (Track B).
- `POST /verify` returns `chain_intact: true`.
- A rendered ECOA notice + `manifest.json` with `hash_chain_verified: true`.
- Anything that **didn't** match these docs — that's the most useful feedback;
  this quickstart is itself under test.
