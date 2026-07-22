/**
 * Span lifecycle types shared between the builder and context propagation.
 * Keeping them in their own module avoids the circular dep we'd hit if
 * propagation imported from builder and builder imported from propagation.
 */

import type {
  AgentCaptureSpan,
  ComplianceMetadata,
  Attributes as TypedAttributes,
} from "../schema/span.js";

export type SpanType = AgentCaptureSpan["type"];
export type SpanStatus = AgentCaptureSpan["status"];

/**
 * Mutable in-progress span state. Held by the builder between open() and
 * close(). Lives in the AsyncLocalStorage context so inner spans can
 * locate their parent.
 */
export interface OpenSpan {
  spanId: string;
  parentSpanId: string | null;
  trajectoryId: string;
  name: string;
  type: SpanType;
  startTime: Date;
  attributes: TypedAttributes;
  compliance: ComplianceMetadata;
  inputs: unknown;
  /** Backreference to the parent OpenSpan; null at the trajectory root. */
  parent: OpenSpan | null;
  /** Child spans buffered here until this one closes; stamped with this
   *  span's content_hash at that moment, then shipped. */
  pending: AgentCaptureSpan[];
}
