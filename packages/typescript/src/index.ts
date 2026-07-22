/**
 * @agent-capture/sdk — trajectory capture engine for AI agent compliance.
 *
 * Public surface mirrors the Python package. Most users only need:
 *
 *     import { traced } from "@agent-capture/sdk";
 *     import { FileExporter, configure } from "@agent-capture/sdk";
 *
 * See ../../docs/architecture.md for the design rationale. Schema types are
 * generated from schemas/span.schema.json by ./scripts/generate_ts_types.sh.
 */

export type {
  AgentCaptureSpan,
  Attributes,
  ComplianceMetadata,
  ErrorInfo,
  ModelCallAttributes,
  ToolCallAttributes,
  RetrievalAttributes,
  PlannerStepAttributes,
  SubAgentInvocationAttributes,
  HumanApprovalAttributes,
  SideEffectAttributes,
  PolicyCheckAttributes,
} from "./schema/span.js";

export { SCHEMA_VERSION } from "./schema/version.js";

// Configuration entry point.
export { configure, type ConfigureOptions } from "./config.js";

// Public canonical hashing — for tests, golden fixtures, cross-language verification.
export {
  canonicalBytes,
  canonicalJson,
  contentHash,
  type CanonicalOptions,
} from "./schema/canonical.js";

// Context propagation.
export {
  currentParent,
  spanScope,
  modelCallSuppressed,
  suppressModelCallCapture,
} from "./context/propagation.js";
export {
  inject,
  extract,
  TRACEPARENT_HEADER,
  TRACESTATE_HEADER,
  type RemoteParent,
} from "./context/w3c.js";

// Span builder.
export { SpanBuilder, type OpenOptions, type CloseOptions, type BuilderOptions } from "./span/builder.js";
export type { OpenSpan, SpanType, SpanStatus } from "./span/types.js";

// Exporters.
export type { SpanExporter } from "./exporter/base.js";
export { FileExporter } from "./exporter/file.js";
export { BoundedQueueExporter, type QueueOptions } from "./exporter/queue.js";
export { HTTPExporter, type HTTPExporterOptions } from "./exporter/http.js";
export { withRetry, defaultRetryPolicy, type RetryPolicy } from "./exporter/retry.js";

// Instrumentation.
export { traced, tracedScope, type TracedOptions } from "./instrumentation/traced.js";

// Redaction.
export {
  FullRedaction,
  HmacFingerprint,
  PassThrough,
  type RedactionStrategy,
  type HmacFingerprintOptions,
} from "./redaction/strategies.js";
export { Policy, parsePolicy, loadPolicy, passThroughPolicy } from "./redaction/policy.js";
export { RedactionFilter, type RedactionFilterOptions } from "./redaction/filter.js";

// Diagnostics.
export { ErrorCode, REMEDIATION, logError, safelog, setLogger } from "./internal/safelog.js";
