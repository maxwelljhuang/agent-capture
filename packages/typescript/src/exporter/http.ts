/**
 * HTTPS batched exporter.
 *
 * Mirrors agent_capture.exporter.http. 5xx/network errors retry with
 * exponential backoff; 4xx drops with a loud AC403 safelog entry.
 * Never throws into the producer.
 */

import { ErrorCode, logError } from "../internal/safelog.js";
import type { AgentCaptureSpan } from "../schema/span.js";

import type { SpanExporter } from "./base.js";
import { defaultRetryPolicy, type RetryPolicy, withRetry } from "./retry.js";

export interface HTTPExporterOptions {
  endpoint: string;
  authToken?: string;
  batchSize?: number;
  batchMaxWaitMs?: number;
  timeoutMs?: number;
  retryPolicy?: RetryPolicy;
  /** Inject for tests. */
  fetch?: typeof fetch;
}

class PermanentHTTPError extends Error {
  constructor(public readonly status: number, public readonly body: string) {
    super(`HTTP ${status}: ${body}`);
  }
}

export class HTTPExporter implements SpanExporter {
  private buffer: AgentCaptureSpan[] = [];
  private firstEnqueueTime: number | null = null;
  private droppedCount = 0;
  private isShutdown = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private inFlight: Promise<void> | null = null;
  private readonly fetchImpl: typeof fetch;
  private readonly batchSize: number;
  private readonly batchMaxWaitMs: number;
  private readonly timeoutMs: number;
  private readonly retryPolicy: RetryPolicy;

  constructor(private readonly options: HTTPExporterOptions) {
    this.fetchImpl = options.fetch ?? globalThis.fetch;
    this.batchSize = options.batchSize ?? 100;
    this.batchMaxWaitMs = options.batchMaxWaitMs ?? 1000;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.retryPolicy = options.retryPolicy ?? defaultRetryPolicy;
  }

  get dropped(): number {
    return this.droppedCount;
  }

  export(span: AgentCaptureSpan): void {
    if (this.isShutdown) return;
    this.buffer.push(span);
    if (this.firstEnqueueTime === null) {
      this.firstEnqueueTime = Date.now();
    }
    if (this.buffer.length >= this.batchSize) {
      void this.flush();
    } else {
      this.ensureTimer();
    }
  }

  private ensureTimer(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.batchMaxWaitMs);
  }

  private async flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.buffer.length === 0) {
      this.firstEnqueueTime = null;
      return;
    }
    const batch = this.buffer;
    this.buffer = [];
    this.firstEnqueueTime = null;
    const send = this.sendWithRetry(batch);
    this.inFlight = send.finally(() => {
      if (this.inFlight === send) this.inFlight = null;
    });
    await send;
  }

  private async sendWithRetry(batch: AgentCaptureSpan[]): Promise<void> {
    let payload: string;
    try {
      payload = JSON.stringify({ spans: batch });
    } catch (exc) {
      logError(
        ErrorCode.AC402,
        `HTTPExporter: serialization failed; dropping ${batch.length} spans`,
        exc,
      );
      this.droppedCount += batch.length;
      return;
    }
    try {
      await withRetry(
        async () => {
          const headers: Record<string, string> = {
            "content-type": "application/json",
          };
          if (this.options.authToken !== undefined) {
            headers.authorization = `Bearer ${this.options.authToken}`;
          }
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);
          let resp: Response;
          try {
            resp = await this.fetchImpl(this.options.endpoint, {
              method: "POST",
              body: payload,
              headers,
              signal: controller.signal,
            });
          } finally {
            clearTimeout(timeoutId);
          }
          if (resp.status >= 400 && resp.status < 500) {
            const body = await resp.text().catch(() => "");
            throw new PermanentHTTPError(resp.status, body.slice(0, 512));
          }
          if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}`);
          }
        },
        {
          policy: this.retryPolicy,
          retryable: (exc) => !(exc instanceof PermanentHTTPError),
        },
      );
    } catch (exc) {
      if (exc instanceof PermanentHTTPError) {
        logError(
          ErrorCode.AC403,
          `HTTPExporter: ledger rejected batch of ${batch.length} spans with HTTP ${exc.status}: ${exc.body}`,
        );
      } else {
        logError(
          ErrorCode.AC404,
          `HTTPExporter: dropping ${batch.length} spans after retries`,
          exc,
        );
      }
      this.droppedCount += batch.length;
    }
  }

  async shutdown(_timeoutMs: number = 5000): Promise<void> {
    if (this.isShutdown) return;
    this.isShutdown = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    await this.flush();
    await this.inFlight;
  }
}
