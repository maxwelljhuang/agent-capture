/**
 * JSON-lines file exporter — for development, testing, air-gapped deploys.
 * Production should wrap with BoundedQueueExporter so disk I/O never
 * blocks the agent's hot path.
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

import { ErrorCode, logError } from "../internal/safelog.js";
import type { AgentCaptureSpan } from "../schema/span.js";

import type { SpanExporter } from "./base.js";

export class FileExporter implements SpanExporter {
  private closed = false;

  constructor(private readonly path: string) {
    mkdirSync(dirname(path), { recursive: true });
  }

  export(span: AgentCaptureSpan): void {
    if (this.closed) return;
    try {
      appendFileSync(this.path, JSON.stringify(span) + "\n", "utf-8");
    } catch (exc) {
      logError(ErrorCode.AC401, `FileExporter.export failed`, exc);
    }
  }

  async shutdown(_timeoutMs: number = 5000): Promise<void> {
    this.closed = true;
  }
}
