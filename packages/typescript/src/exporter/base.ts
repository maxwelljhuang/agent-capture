/**
 * SpanExporter interface — every destination implements this.
 * Mirrors agent_capture.exporter.base.
 */

import type { AgentCaptureSpan } from "../schema/span.js";

export interface SpanExporter {
  /**
   * Persist or ship a single finalized span. Must not raise into the caller.
   * Internal failures log via {@link logError}.
   */
  export(span: AgentCaptureSpan): void;

  /** Flush buffered state and release resources. Idempotent. */
  shutdown(timeoutMs?: number): Promise<void>;
}
