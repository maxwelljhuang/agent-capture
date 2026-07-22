/**
 * Bounded queue exporter — non-blocking handoff with background drain.
 *
 * Mirrors agent_capture.exporter.queue. Critical span types
 * (human_approval, side_effect) skip the drop policy and instead wait
 * briefly for the worker to catch up.
 */

import { ErrorCode, logError } from "../internal/safelog.js";
import type { AgentCaptureSpan } from "../schema/span.js";

import type { SpanExporter } from "./base.js";

const CRITICAL_TYPES: ReadonlySet<AgentCaptureSpan["type"]> = new Set([
  "human_approval",
  "side_effect",
]);

export interface QueueOptions {
  maxSize?: number;
  criticalBlockTimeoutMs?: number;
}

export class BoundedQueueExporter implements SpanExporter {
  private readonly maxSize: number;
  private readonly criticalBlockTimeoutMs: number;
  private buffer: AgentCaptureSpan[] = [];
  private droppedCount = 0;
  private isShutdown = false;
  private draining: Promise<void> | null = null;
  private drainRequested = false;

  constructor(
    private readonly inner: SpanExporter,
    options: QueueOptions = {},
  ) {
    this.maxSize = options.maxSize ?? 10_000;
    this.criticalBlockTimeoutMs = options.criticalBlockTimeoutMs ?? 1000;
  }

  get dropped(): number {
    return this.droppedCount;
  }

  export(span: AgentCaptureSpan): void {
    if (this.isShutdown) {
      this.droppedCount++;
      return;
    }
    if (this.buffer.length >= this.maxSize) {
      this.handleFull(span);
    } else {
      this.buffer.push(span);
    }
    this.scheduleDrain();
  }

  private handleFull(span: AgentCaptureSpan): void {
    if (CRITICAL_TYPES.has(span.type)) {
      // We can't truly block in a sync JS call, so we attempt synchronous
      // drain by yielding to the inner exporter immediately. If still
      // full, log loud and drop. In Node async-first code this is the
      // honest model — there is no equivalent to Python's blocking put().
      this.flushSync();
      if (this.buffer.length >= this.maxSize) {
        logError(
          ErrorCode.AC406,
          `Dropped CRITICAL span (${span.type}) — queue saturated and sync flush could not catch up`,
        );
        this.droppedCount++;
        return;
      }
      this.buffer.push(span);
      return;
    }
    // Non-critical: drop oldest, enqueue new.
    this.buffer.shift();
    this.droppedCount++;
    this.buffer.push(span);
  }

  /** Drain the buffer through the inner exporter on the same tick. */
  private flushSync(): void {
    const batch = this.buffer;
    this.buffer = [];
    for (const s of batch) {
      try {
        this.inner.export(s);
      } catch (exc) {
        logError(ErrorCode.AC405, "inner exporter failed", exc);
      }
    }
  }

  private scheduleDrain(): void {
    if (this.draining !== null) return;
    this.drainRequested = true;
    // Microtask so we drain promptly without recursing on every export().
    this.draining = Promise.resolve().then(() => {
      try {
        while (this.drainRequested) {
          this.drainRequested = false;
          this.flushSync();
        }
      } finally {
        this.draining = null;
      }
    });
  }

  async shutdown(timeoutMs: number = 5000): Promise<void> {
    if (this.isShutdown) return;
    this.isShutdown = true;
    const start = Date.now();
    // Drain any in-flight drains plus our own remaining buffer.
    while (this.draining !== null || this.buffer.length > 0) {
      await this.draining;
      if (this.buffer.length > 0) this.flushSync();
      if (Date.now() - start > timeoutMs) {
        if (this.buffer.length > 0) {
          logError(ErrorCode.AC407, "BoundedQueueExporter.shutdown: queue not drained at timeout");
        }
        break;
      }
    }
    await this.inner.shutdown(timeoutMs);
  }
}
