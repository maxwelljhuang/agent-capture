# Report generator wish list

The downstream reporting layer draws every regulatory artifact from the
fields the recorder captures. This doc is the contract: if a regulator
asks a question, this is the span field that answers it. Anything not
captured here cannot be reconstructed later, which is why
[architecture doc §4.3](./architecture.md) classifies these as
"capture at creation, not reconstruction."

Each section names a regulatory artifact, lists the questions the
generator must answer, and maps every answer to a specific
`schema.Span` field path.

## Universal — every report uses these

These are pre-requisites for *any* downstream report. The Week 6
verifier (`scripts/verify_trajectory.py`) checks every span in a
trajectory satisfies these.

| Question | Span field |
| --- | --- |
| What span is this? | `span_id`, `parent_span_id`, `trajectory_id`, `type`, `name` |
| When did it run? | `start_time`, `end_time` (UTC, microsecond precision) |
| Did it succeed? | `status` ∈ {ok, error, cancelled}; `error.{error_type, message}` when status=error |
| Which compliance policy was in force? | `compliance.policy_version_active` |
| Which agent version produced it? | `compliance.agent_version` |
| Which customer environment? | `compliance.end_customer_id` |
| Which regulations apply? | `compliance.regulatory_regime` (non-empty list) |
| How long must it be retained? | `compliance.retention_class` |
| What sensitivity class? | `compliance.data_classification` |
| Is the chain tamper-evident? | `provenance.content_hash` (SHA-256), `provenance.parent_content_hash` (null only for root), `provenance.schema_version` |

## Adverse Action Notice — ECOA / Regulation B + FCRA

Triggered when a credit application is denied, or approved on terms less
favorable than requested. The notice must include the **specific
reasons** for the action (12 CFR §1002.9(b)(2)) and identify the
**consumer reporting agency** used (FCRA §615).

| Question the generator must answer | Span(s) it reads from |
| --- | --- |
| What was the final decision? | Root `planner_step.attributes.chosen_option` and `decision_rationale` |
| What were the principal reasons? | The `model_call` span's `outputs` (typically a structured `primary_reason` + `secondary_reasons`) and its `attributes.prompt_template_version` (so we can re-read the prompt's reason taxonomy) |
| Which CRAs were consulted? | Every `retrieval.attributes.source_identifier` along the trajectory |
| When was the decision made? | Root span's `end_time`, or the `human_approval.attributes.decision_timestamp` if there was one |
| Which human reviewed? | `human_approval.attributes.approver_identity` + `approver_role` |
| Was a notice actually sent? | `side_effect.attributes.action_type == "document.mail"` with `success=true` and an `idempotency_key` |
| Was the decision policy-compliant at the time? | `policy_check.attributes.{policy_name, policy_version, result, rule_details}` for `ecoa.protected_class.*` checks |
| Was the regime applicable? | `compliance.regulatory_regime` ⊇ `["ECOA", "FCRA"]` |
| Who is the applicant? (HMAC-fingerprinted) | `compliance.subject_id` |

## Model Governance — Federal Reserve SR 11-7

Banks must document the use of any model that materially affects a
credit decision: model identity, prompt, parameters, inputs, outputs,
and the governance approval the model is operating under.

| Question | Span field |
| --- | --- |
| Which model? | `model_call.attributes.model_name`, `model_version`, `provider` |
| Which approved model card? | `model_call.compliance.model_card_version` |
| Which prompt template version? | `model_call.attributes.prompt_template_id` + `prompt_template_version`; also `compliance.prompt_template_version` |
| Reproducibility parameters | `model_call.attributes.temperature`, `max_tokens` |
| Token usage (audit / cost) | `model_call.attributes.input_tokens`, `output_tokens`, `total_tokens` |
| What was the model's input? | `model_call.inputs` (post-redaction) |
| What was the model's output? | `model_call.outputs` (post-redaction) |
| Was the prompt under change control? | `compliance.policy_version_active` + the prompt version |

## BSA/AML — Suspicious Activity Report support

If a fraud-flagged trajectory eventually produces a SAR, the report
generator pulls from this slice. The recorder is not the SAR system, but
it must capture enough that the SAR system can construct the narrative.

| Question | Span field |
| --- | --- |
| Who is the customer? | `compliance.subject_id` (fingerprinted) |
| What transaction triggered review? | `tool_call.attributes.arguments` (post-redaction, with account/routing HMAC'd so they remain re-fingerprintable) |
| What risk score / why? | `policy_check.attributes.rule_details` for `bsa.aml.*` checks |
| What action was taken? | `side_effect.attributes.action_type` (e.g. `account.freeze`) with `success` + `idempotency_key` |
| Which agent / model recommended it? | `model_call.attributes.model_name` + `model_card_version` |
| Was a human-in-the-loop applied? | Presence (or absence) of a `human_approval` span between the policy_check and the side_effect |
| Which regulations? | `compliance.regulatory_regime` ⊇ `["BSA_AML"]` |

## Sub-agent attribution

When agent A invokes agent B, the report generator must be able to
attribute decisions to whichever agent actually made them.

| Question | Span field |
| --- | --- |
| Which sub-agent was invoked? | `sub_agent_invocation.attributes.sub_agent_identity` + `sub_agent_version` |
| What did the sub-agent do? | Spans nested under the `sub_agent_invocation` via `parent_span_id` |
| Were the sub-agent's spans correctly attributed? | `parent_span_id` chain → `sub_agent_invocation` → ancestor; `trajectory_id` matches the root's |

## What the recorder deliberately does not capture

For completeness, things the downstream generators must source elsewhere:

- The actual letter PDF — the recorder captures the `side_effect`
  proving it was mailed, not the bytes. The documents system owns those.
- Raw PII values — by design, the recorder ships only `[REDACTED:...]`
  sentinels or HMAC fingerprints. The customer's secure store owns the
  originals.
- Real-time risk decisions — the recorder observes; the enforcement
  engine acts on the stream.
- Multi-trajectory aggregates (e.g. "how many denials this month") —
  the query layer reads from the ledger; the recorder produces single
  trajectories.

## Verification

Every assertion in this document is encoded in
`scripts/verify_trajectory.py`. The Week 6 integration test runs the
loan-denial example end-to-end and pipes its trajectory through that
verifier; a passing run means every report generator listed above can
draw its required fields from a real recorder output.
