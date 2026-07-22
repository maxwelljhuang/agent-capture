/* eslint-disable */
/* This file is generated. Do not edit.
 * Source: schemas/span.schema.json
 * Regenerate: ./scripts/generate_ts_types.sh
 */

/**
 * Per-type attribute payload. The 'kind' field matches the 'type' field above.
 */
export type Attributes =
  | ModelCallAttributes
  | ToolCallAttributes
  | RetrievalAttributes
  | PlannerStepAttributes
  | SubAgentInvocationAttributes
  | HumanApprovalAttributes
  | SideEffectAttributes
  | PolicyCheckAttributes;
export type InputTokens = number | null;
export type Kind = "model_call";
export type MaxTokens = number | null;
export type ModelName = string;
export type ModelVersion = string | null;
export type OutputTokens = number | null;
export type PromptTemplateId = string | null;
export type PromptTemplateVersion = string | null;
/**
 * e.g. 'anthropic', 'openai', 'bedrock', 'vertex'.
 */
export type Provider = string;
export type Temperature = number | null;
export type TotalTokens = number | null;
export type Arguments = {
  [k: string]: unknown;
} | null;
export type Kind1 = "tool_call";
export type ToolName = string;
export type ToolSchemaVersion = string | null;
export type Kind2 = "retrieval";
export type Query = string | null;
export type RelevanceScores = number[];
export type ReturnedDocumentIds = string[];
/**
 * Vector DB name, doc store id, external API url, etc.
 */
export type SourceIdentifier = string;
export type ChosenOption = string | null;
export type DecisionRationale = string | null;
export type Kind3 = "planner_step";
export type OptionsConsidered = string[];
export type Kind4 = "sub_agent_invocation";
export type SubAgentIdentity = string;
export type SubAgentVersion = string | null;
export type ApproverIdentity = string;
export type ApproverRole = string;
/**
 * Stable identifier of the artifact reviewed (e.g. document hash).
 */
export type ArtifactReviewed = string;
export type Decision = "approved" | "rejected" | "escalated";
/**
 * ISO 8601 UTC timestamp of the decision.
 */
export type DecisionTimestamp = string;
export type Kind5 = "human_approval";
/**
 * Optional cryptographic signature over the decision. Format is policy-defined.
 */
export type Signature = string | null;
/**
 * e.g. 'email.send', 'payment.transfer', 'record.update'.
 */
export type ActionType = string;
export type IdempotencyKey = string | null;
export type Kind6 = "side_effect";
/**
 * Human-readable summary of what was sent. Full payload lives in span inputs.
 */
export type PayloadSummary = string | null;
/**
 * Did the external action succeed?
 */
export type Success = boolean;
/**
 * The external system that received the effect.
 */
export type TargetSystem = string;
export type Kind7 = "policy_check";
export type PolicyName = string;
export type PolicyVersion = string;
export type Result = "pass" | "fail" | "warn" | "not_applicable";
export type RuleDetails = {
  [k: string]: unknown;
} | null;
/**
 * Version of the agent that produced this span.
 */
export type AgentVersion = string;
/**
 * Sensitivity classification of the payload.
 */
export type DataClassification = "public" | "internal" | "PII" | "PCI" | "MNPI" | "PHI";
/**
 * Which regulated customer's environment this is running in.
 */
export type EndCustomerId = string;
/**
 * Approved model card governing this model use. Only meaningful for model_call spans.
 */
export type ModelCardVersion = string | null;
/**
 * Identifier of the compliance policy bundle in force when the span was created.
 */
export type PolicyVersionActive = string;
/**
 * Exact version of the prompt template used. Only meaningful for model_call spans.
 */
export type PromptTemplateVersion1 = string | null;
/**
 * Regulatory regimes recognized by the v1 schema.
 *
 * The list is intentionally finance-first. Add new entries as verticals
 * expand; never repurpose existing values. Reporting downstream keys off
 * these strings.
 */
export type RegulatoryRegime1 = "ECOA" | "FCRA" | "SR_11-7" | "UDAAP" | "GLBA" | "BSA_AML" | "HIPAA" | "GDPR" | "CCPA";
/**
 * Applicable regulations for this trajectory.
 */
export type RegulatoryRegime = RegulatoryRegime1[];
/**
 * Retention classification governing this span's lifecycle.
 */
export type RetentionClass = "standard" | "extended" | "litigation_hold" | "transient";
/**
 * The entity the agent is acting on (loan applicant, account holder, etc.), redacted appropriately.
 */
export type SubjectId = string | null;
/**
 * Schema version of any tool involved. Only meaningful for tool_call spans.
 */
export type ToolSchemaVersion1 = string | null;
/**
 * High-resolution UTC end time.
 */
export type EndTime = string;
/**
 * Fully-qualified exception type name.
 */
export type ErrorType = string;
/**
 * Human-readable error message, post-redaction.
 */
export type Message = string;
/**
 * Optional stack trace, post-redaction. Capture is configurable.
 */
export type StackTrace = string | null;
/**
 * Human-readable label for this span.
 */
export type Name = string;
/**
 * Identifier of the parent span. Null only for the trajectory root.
 */
export type ParentSpanId = string | null;
/**
 * Hex SHA-256 of the canonical serialized span.
 */
export type ContentHash = string;
/**
 * Hex SHA-256 of the parent span's canonical form. Null for trajectory roots.
 */
export type ParentContentHash = string | null;
/**
 * Span schema version. See SCHEMA_VERSION in this module.
 */
export type SchemaVersion = string;
/**
 * Unique identifier for this span. 16 hex chars (OTel 8-byte span id).
 */
export type SpanId = string;
/**
 * High-resolution UTC start time.
 */
export type StartTime = string;
/**
 * Terminal status.
 */
export type SpanStatus = "ok" | "error" | "cancelled";
/**
 * Identifier shared by every span in this trajectory. 32 hex chars (OTel 16-byte trace id).
 */
export type TrajectoryId = string;
/**
 * Kind of step. Matches attributes.kind.
 */
export type SpanType =
  | "model_call"
  | "tool_call"
  | "retrieval"
  | "planner_step"
  | "sub_agent_invocation"
  | "human_approval"
  | "side_effect"
  | "policy_check";

/**
 * agent-capture span schema v1.0.0. Generated from the Pydantic models in packages/python/src/agent_capture/schema/. Do not edit by hand.
 */
export interface AgentCaptureSpan {
  attributes: Attributes;
  compliance: ComplianceMetadata;
  end_time: EndTime;
  /**
   * Required when status is 'error'.
   */
  error?: ErrorInfo | null;
  inputs?: unknown;
  name: Name;
  outputs?: unknown;
  parent_span_id?: ParentSpanId;
  provenance: ProvenanceFields;
  span_id: SpanId;
  start_time: StartTime;
  status?: SpanStatus;
  trajectory_id: TrajectoryId;
  type: SpanType;
}
/**
 * Attributes for an LLM invocation.
 */
export interface ModelCallAttributes {
  input_tokens?: InputTokens;
  kind?: Kind;
  max_tokens?: MaxTokens;
  model_name: ModelName;
  model_version?: ModelVersion;
  output_tokens?: OutputTokens;
  prompt_template_id?: PromptTemplateId;
  prompt_template_version?: PromptTemplateVersion;
  provider: Provider;
  temperature?: Temperature;
  total_tokens?: TotalTokens;
}
/**
 * Attributes for invocation of a tool the agent has access to.
 */
export interface ToolCallAttributes {
  arguments?: Arguments;
  kind?: Kind1;
  return_value?: unknown;
  tool_name: ToolName;
  tool_schema_version?: ToolSchemaVersion;
}
/**
 * Attributes for fetching information from a knowledge source.
 */
export interface RetrievalAttributes {
  kind?: Kind2;
  query?: Query;
  relevance_scores?: RelevanceScores;
  returned_document_ids?: ReturnedDocumentIds;
  source_identifier: SourceIdentifier;
}
/**
 * Attributes for an internal decision point in the agent's control flow.
 */
export interface PlannerStepAttributes {
  chosen_option?: ChosenOption;
  decision_rationale?: DecisionRationale;
  kind?: Kind3;
  options_considered?: OptionsConsidered;
}
/**
 * Attributes for invoking another agent. The sub-agent's spans nest under this one.
 */
export interface SubAgentInvocationAttributes {
  kind?: Kind4;
  sub_agent_identity: SubAgentIdentity;
  sub_agent_version?: SubAgentVersion;
}
/**
 * Attributes for a human reviewing and approving (or rejecting) a proposed action.
 */
export interface HumanApprovalAttributes {
  approver_identity: ApproverIdentity;
  approver_role: ApproverRole;
  artifact_reviewed: ArtifactReviewed;
  decision: Decision;
  decision_timestamp: DecisionTimestamp;
  kind?: Kind5;
  signature?: Signature;
}
/**
 * Attributes for an action that affected the outside world.
 *
 * Distinct from ``tool_call`` because the question is about external state
 * change, not the agent's local action. A regulator asking 'did the agent
 * actually send the denial letter?' is asking about this span type.
 */
export interface SideEffectAttributes {
  action_type: ActionType;
  idempotency_key?: IdempotencyKey;
  kind?: Kind6;
  payload_summary?: PayloadSummary;
  success: Success;
  target_system: TargetSystem;
}
/**
 * Attributes for an automated rule evaluation.
 */
export interface PolicyCheckAttributes {
  kind?: Kind7;
  policy_name: PolicyName;
  policy_version: PolicyVersion;
  result: Result;
  rule_details?: RuleDetails;
}
/**
 * Compliance metadata. Required on every span (Section 4.3).
 */
export interface ComplianceMetadata {
  agent_version: AgentVersion;
  data_classification?: DataClassification;
  end_customer_id: EndCustomerId;
  model_card_version?: ModelCardVersion;
  policy_version_active: PolicyVersionActive;
  prompt_template_version?: PromptTemplateVersion1;
  regulatory_regime?: RegulatoryRegime;
  retention_class?: RetentionClass;
  subject_id?: SubjectId;
  tool_schema_version?: ToolSchemaVersion1;
}
/**
 * Structured error captured when a span's status is ``error``.
 */
export interface ErrorInfo {
  error_type: ErrorType;
  message: Message;
  stack_trace?: StackTrace;
}
/**
 * Hash chain entries for the downstream ledger (Section 4.4).
 */
export interface ProvenanceFields {
  content_hash: ContentHash;
  parent_content_hash?: ParentContentHash;
  schema_version?: SchemaVersion;
}
