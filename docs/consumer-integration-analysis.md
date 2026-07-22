# Consumer integration analysis — LearnSuite `ai-employee` & `heyC`

**Status:** analysis + `ai-employee` integration implemented (2026-06-06).
**Scope:** how two existing products can leverage `agent-capture` for
decision-trajectory traceability, what it costs, and where the SDK falls short.

This is an advisory/architecture document. It does not change any
`agent-capture` code; it records the assessment and the integration that was
built into the `ai-employee` repo against the SDK's public contract.

---

## 1. Executive summary

| | `ai-employee` (LearnSuite) | `heyC` |
|---|---|---|
| **Stack** | Python · FastAPI · LangGraph · LiteLLM | C#/.NET 9 · Azure OpenAI · custom orchestration |
| **Domain** | HOA management + tutoring | K-12 education (FERPA) |
| **Fit with agent-capture** | **High** — same language, adapters exist | Conceptually high, mechanically blocked (no .NET SDK) |
| **Integration effort** | ~1 day (done) | Weeks if native; days via sidecar |
| **Top value delivered** | Tamper-evident audit + enforcement gating on side effects | Immutable audit over today's mutable Cosmos store |
| **Out-of-box reports** | SR 11-7 plausible; ECOA/FCRA n/a | None applicable (FERPA not modeled) |
| **Recommendation** | **Integrate** (implemented) | **Sidecar against unmodified ledger**, defer schema/reporter work |

The recorder + ledger + enforcement layers are **domain-agnostic** and deliver
real traceability value to both products (tamper-evident trajectories,
independent `POST /verify`, Ed25519 attestations, `/access-log`, retention).
The **reporting layer is lending-specific** (ECOA Adverse-Action, SR 11-7 Model
Governance) and mostly does not apply to either consumer.

---

## 2. What agent-capture exposes (the integration seams)

Four in-repo layers; the dashboard ("Kelp") is a separate repo that only reads.

| Layer | What you get | How you consume it |
|---|---|---|
| **Recorder (L1)** | In-process trajectory capture + in-process redaction; "never breaks the host" | `pip install agent-capture` (Python) or TS SDK; `@traced`, LangGraph/CrewAI adapters, Anthropic/OpenAI client wrappers |
| **Ledger (L2)** | Append-only Postgres, hash-chain + Ed25519 Merkle attestations, retention, role-scoped reads | SDK `HTTPExporter` → `POST /spans` (8443, Bearer ingest token) |
| **Reporting (L3)** | ECOA Adverse-Action + SR 11-7 notices | `agent_capture_reporter` CLI over the ledger |
| **Enforcement (L5)** | Gate `side_effect` / `human_approval`: advisory → hold → block + review queue | Separate service (8475); recorder calls `POST /verdict` |

**The 8 span types** are the vocabulary you map your agent onto:
`model_call`, `tool_call`, `retrieval`, `planner_step`,
`sub_agent_invocation`, `human_approval`, `side_effect`, `policy_check`.
The last three are compliance-specific and the report generators key off them.

**Two structural facts that shape every integration:**

1. **SDKs exist only for Python and TypeScript.** Ledger ingest is plain
   HTTPS, but `content_hash` is computed over a canonical serialization
   (`schema/canonical.py`) that Python and TS emit **byte-identically**,
   enforced by golden fixtures. A non-Python/TS client cannot simply "POST
   JSON" — it must reproduce that canonicalization exactly or ingest rejects it
   (`LE003` content-hash mismatch).
2. **The schema is frozen and finance-shaped.**
   `regulatory_regime ∈ {ECOA, FCRA, SR_11-7, UDAAP, GLBA, BSA_AML, HIPAA,
   GDPR, CCPA}` — **no FERPA, no HOA**. Reporters ship only ECOA + SR 11-7.

---

## 3. `ai-employee` (LearnSuite) — high fit, **integration implemented**

### 3.1 What it is
Multi-tenant agentic platform. Core agent loop is a single-call tool-use loop
(`backend/app/ai/agent/base.py`); orchestration in
`services/orchestrator.py`; LLM access via **LiteLLM** (`ai/llm/router.py`),
not LangChain LLM classes. Side effects: email (Resend), Twilio phone calls,
HTTP. Multi-tenant via Postgres row-level security keyed on `tenant_id`.

### 3.2 Span mapping

| ai-employee concept | File | Span type |
|---|---|---|
| `BaseAgent.run` (one turn) | `ai/agent/base.py` | `planner_step` (trajectory root) |
| LiteLLM call (`_once`) | `ai/agent/base.py` | `model_call` |
| Tool execution | `ai/agent/base.py:_execute_single_tool_call` | `tool_call` |
| RAG search | `services/orchestrator.py` | `retrieval` |
| WorkflowAgent → runner | `services/orchestrator.py` | `sub_agent_invocation` |
| `WaitingForInput` (on resume) | `services/workflow/runner.py` | `human_approval` |
| Email / Twilio / HTTP write | `services/email/sender.py`, `workflow/tools.py` | `side_effect` |
| Consent / call-policy gate | `models/user_consent.py`, `models/calling.py` | `policy_check` |

### 3.3 What was implemented (this change set, in the ai-employee repo)

- **`backend/app/observability/capture.py`** (new) — single SDK import point.
  - `init_capture()`: env-driven destination (ledger / file / disabled).
  - `compliance_for(context)`: builds per-tenant `ComplianceMetadata`
    (`end_customer_id = tenant_id`, regime `[CCPA]`, class `PII`).
  - A **contextvar** (`active_compliance`) set at the turn root and read by the
    deep span sites, so every span in a trajectory shares `end_customer_id`
    without threading the tenant through every signature.
  - **No-op fallback**: if `agent-capture` is not installed, `traced`/`SpanType`
    degrade to transparent no-ops so the host imports and runs unchanged.
- **`backend/app/main.py`** — calls `init_capture()` in the lifespan startup.
- **`backend/app/ai/agent/base.py`**:
  - `run()` wrapped as a root `planner_step`; original body → `_run_inner`.
  - `model_call` span per LLM attempt in `_once()` (LiteLLM isn't
    LangChain-native, so no adapter would capture it — explicit wrap is the
    correct seam). Sets model/provider/temperature/token attributes.
  - `tool_call` span around each tool handler, capturing args + return.
- **`backend/app/services/email/sender.py`** — `side_effect` span
  (`action_type="email.send"`, `target_system="resend"`, `success`,
  `idempotency_key`). This is the compliance-critical span no adapter infers.
- **`backend/requirements.txt`** — documented (commented) dependency line;
  install path noted, kept commented so `pip install -r` stays portable.

**Follow-up batch (also implemented):**
- **`backend/app/services/workflow/runner.py`** — `human_approval` span in
  `resume()`: a paused workflow resolved by human input. Emitted as its own
  trajectory root (resume is a separate entry point); decision mapped to
  `approved` when input is supplied, with the raw answer in `outputs` rather
  than coerced into the enum.
- **`backend/app/services/calling/profile.py`** — `policy_check` span in
  `build_call_context()`: records the resolved call-decision policy;
  `requires_confirmation ⇒ warn`, else `pass`; autonomy details in
  `rule_details`.
- **`backend/app/services/tools/phone_call.py`** — `side_effect` span around
  the Twilio dial (`action_type="phone.call"`, `target_system="twilio"`,
  `success`, `idempotency_key=call_sid`).
- **`backend/app/services/workflow/tools.py`** — `side_effect` span in
  `HttpTool` (`action_type="http.request"`, `success=status<400`).

### 3.4 Verification performed
- All edited files compile.
- No-op path exercised with the SDK absent (CM, decorator, `set_outputs`).
- Real-SDK path exercised with `AGENT_CAPTURE_FILE`: a 3-span trajectory
  (`planner_step → model_call`, `tool_call`) was produced; `model_call` and
  `tool_call` correctly nested under the root via contextvars; tenant
  propagated to children; **hash-chain intact** (each child's
  `parent_content_hash` matched its parent's `content_hash`).
- Follow-up batch verified the same way: `human_approval`, `policy_check`, and
  both `side_effect` (`phone.call`, `http.request`) spans validate against the
  real typed-attribute models and persist with valid content hashes.

### 3.5 Left for a follow-up
- Link the `human_approval` (resume) span back to its originating run via W3C
  trace context — currently it stands as its own trajectory root.
- Production `RedactionFilter` from a customer-authored `policy.yaml`.
- Set `AGENT_CAPTURE_LEDGER_URL` + `AGENT_CAPTURE_LEDGER_TOKEN` to ship to a
  real ledger; until then it is a no-op.

### 3.6 Caveats
- ECOA/FCRA reporting won't fire (HOA dues/ARC aren't credit decisions). Value
  is traceability + enforcement, plus SR 11-7 model governance if framed.
- Per-span overhead budget (<1ms p99) assumes the recorder stays off the hot
  path; the LiteLLM wrap adds no sync hop, but confirm under load.

---

## 4. `heyC` — strong conceptual fit, two real gaps

### 4.1 What it is
C#/.NET 9, Azure OpenAI, custom RAG orchestration
(`EnhancedOpenAIService`, `RagEvaluator`, `GuardrailService`). Already captures
intermediate steps (`IntermediateStepDocument`, `RagEvaluationDocument`) into
**Cosmos DB** (mutable) + Postgres + logs. Domain: K-12 education, FERPA,
student/teacher PII.

### 4.2 Span mapping (heyC already captures most of this)

| heyC concept | Span type |
|---|---|
| `GuardrailService` / `AIGuardrailPreprocessor` | `policy_check` |
| Hybrid search / neighbor expansion | `retrieval` |
| Azure OpenAI completion | `model_call` |
| `ProgressiveFallbackService` branches | `planner_step` |
| `IntermediateStepDocument` (already nested via `ParentStepId`) | `parent_span_id` tree |
| Cosmos conversation write | `side_effect` |
| User feedback (pos/neg) | `human_approval` (loosely) |

### 4.3 Value (independent of the gaps)
heyC's actual pain — **mutable Cosmos, decisions scattered across three
stores, no immutable audit** — is solved by the **ledger alone**, which is
regime-agnostic. heyC gets ~80% of the value (tamper-evidence, `verify`,
attestations, `/access-log`, retention) **without** touching either gap below.

### 4.4 Gap 1 — no .NET SDK → **solve with a sidecar, do not port**

Building a native .NET recorder means re-implementing `canonical.py`
**byte-identically** in C# (a third reference impl on a "frozen-forever"
schema — a permanent tax on every future change), plus the redaction floor,
bounded queue, retry, never-raise wrapper, and W3C context propagation.

**Recommended instead:** a thin **Python sidecar** that receives heyC's
already-structured events (`IntermediateStepDocument`, `RagEvaluationDocument`,
guardrail decisions) over `POST /events` and uses the *real* SDK to
build → redact → hash → ship. Reuses the one golden-tested canonicalizer.

| | Native .NET SDK | Python sidecar |
|---|---|---|
| Canonical-hash risk | 3rd impl, perpetual drift | reuses tested one |
| Build cost | weeks + ongoing tax | days |
| Maintenance | new language in the repo | one small service |
| Loses | — | inline enforcement gating (heyC's `GuardrailService` already gates inline, so not needed) |

### 4.5 Gap 2 — schema/reporter (FERPA) → **additive, gated on real need**

- **`regulatory_regime` enum lacks FERPA.** Appending an enum value is
  genuinely additive (doesn't break existing spans or hashes). It's a
  schema-*governance* decision ("the schema is forever"), not a code problem —
  do it only when a second education customer is real.
- **No FERPA report generator.** The reporter ships only `ecoa/` and
  `sr_11_7/`. A FERPA artifact is a **new renderer + new field-mapping
  contract** — net-new domain work, not an extension. Worth it **only if FERPA
  filings are a committed product goal**, which most of heyC's value does not
  require.

### 4.6 Recommended sequence for heyC
1. Stand up the **sidecar against an unmodified ledger** → immediate
   tamper-evident audit over heyC's RAG/guardrail trajectories, zero schema
   changes.
2. Add the FERPA enum value **only** when a second education customer lands.
3. Build a FERPA renderer **only** if a FERPA filing becomes a deliverable.
4. **Do not build the .NET SDK** for heyC's current needs.

---

## 5. On supporting .NET in the SDK (scope of the "not worth it" verdict)

The "not worth it" recommendation is **scoped to heyC's immediate need**, not
a blanket product verdict:

- **For heyC today:** a native .NET recorder is the wrong tool — a Python
  sidecar gets the same traceability in days and avoids a third byte-exact
  canonicalizer. **Don't build it.**
- **For agent-capture as a product:** a first-class .NET SDK *could* be
  justified **if** .NET becomes a strategic slice of the addressable
  vendor base (many regulated-finance shops are .NET). That's a roadmap
  decision driven by demand across multiple prospects, with eyes open to the
  real cost: the canonical-serialization contract would then bind **three**
  reference implementations forever, so it should be funded as a maintained
  product surface (its own golden-fixture parity suite mirroring Python↔TS),
  not as a one-off for a single consumer.

In short: **not worth it to unblock one .NET consumer; potentially worth it as
a demand-driven product investment** — and even then, only with the
three-way canonical-parity cost budgeted in.

---

## 6. Decision log

- Use the contextvar pattern (not signature threading, not an instance
  attribute) to propagate per-tenant compliance to deep span sites — safe under
  concurrent requests sharing an agent instance.
- Centralize SDK imports behind `app/observability/capture.py` with no-op
  fallbacks so the host never fails to import when the SDK is absent.
- Wrap `model_call` explicitly rather than rely on the LangGraph adapter,
  because ai-employee's LLM path is LiteLLM, not LangChain.
- For heyC: sidecar over native SDK; defer FERPA schema/reporter work until a
  concrete second customer / filing requirement exists.
