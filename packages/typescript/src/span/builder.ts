/**
 * SpanBuilder — open/close + hash-chain finalization.
 *
 * Mirrors agent_capture.span.builder. Children buffer with their parent
 * until the parent closes; parent then stamps every pending descendant
 * with its content_hash and ships the subtree leaves-first. Root ships
 * last. This is the only ordering consistent with "compute provenance as
 * the last step before export" while keeping content_hash a function of
 * the span's own content alone.
 */

import { randomBytes } from "node:crypto";

import { ErrorCode, logError } from "../internal/safelog.js";
import { currentParent } from "../context/propagation.js";
import type { SpanExporter } from "../exporter/base.js";
import { contentHash } from "../schema/canonical.js";
import { SCHEMA_VERSION } from "../schema/version.js";
import type {
  AgentCaptureSpan,
  ComplianceMetadata,
  ErrorInfo,
  Attributes as TypedAttributes,
} from "../schema/span.js";

import type { OpenSpan, SpanStatus, SpanType } from "./types.js";

function newTrajectoryId(): string {
  return randomBytes(16).toString("hex");
}

function newSpanId(): string {
  return randomBytes(8).toString("hex");
}

/** Format a Date as "YYYY-MM-DDTHH:MM:SS.ffffffZ" matching canonical.ts. */
function isoCanonical(d: Date): string {
  const pad = (n: number, w: number) => String(n).padStart(w, "0");
  return (
    `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1, 2)}-${pad(d.getUTCDate(), 2)}T` +
    `${pad(d.getUTCHours(), 2)}:${pad(d.getUTCMinutes(), 2)}:${pad(d.getUTCSeconds(), 2)}.` +
    `${pad(d.getUTCMilliseconds(), 3)}000Z`
  );
}

export interface OpenOptions {
  name: string;
  type: SpanType;
  attributes: TypedAttributes;
  compliance?: ComplianceMetadata;
  inputs?: unknown;
  /** Explicit parent override (framework adapters that wire by run_id). */
  parent?: OpenSpan | null;
}

export interface CloseOptions {
  outputs?: unknown;
  status?: SpanStatus;
  error?: ErrorInfo;
}

export interface BuilderOptions {
  defaultCompliance?: ComplianceMetadata;
  redactionFilter?: { apply(span: AgentCaptureSpan): AgentCaptureSpan };
}

export class SpanBuilder {
  constructor(
    private readonly exporter: SpanExporter,
    private readonly options: BuilderOptions = {},
  ) {}

  open({
    name,
    type,
    attributes,
    compliance,
    inputs,
    parent,
  }: OpenOptions): OpenSpan {
    const effectiveCompliance = compliance ?? this.options.defaultCompliance;
    if (effectiveCompliance === undefined) {
      throw new Error(
        "No compliance metadata available. Pass compliance or configure({ defaultCompliance })",
      );
    }
    const resolvedParent = parent ?? currentParent();
    const trajectoryId =
      resolvedParent === null ? newTrajectoryId() : resolvedParent.trajectoryId;
    const spanId =
      resolvedParent === null ? trajectoryId.slice(0, 16) : newSpanId();
    const parentSpanId = resolvedParent === null ? null : resolvedParent.spanId;
    return {
      spanId,
      parentSpanId,
      trajectoryId,
      name,
      type,
      startTime: new Date(),
      attributes,
      compliance: effectiveCompliance,
      inputs: inputs ?? null,
      parent: resolvedParent,
      pending: [],
    };
  }

  close(openSpan: OpenSpan, { outputs, status, error }: CloseOptions = {}): AgentCaptureSpan | null {
    try {
      return this.closeImpl(openSpan, outputs ?? null, status ?? "ok", error ?? null);
    } catch (exc) {
      logError(ErrorCode.AC101, "SpanBuilder.close failed", exc);
      return null;
    }
  }

  private closeImpl(
    openSpan: OpenSpan,
    outputs: unknown,
    status: SpanStatus,
    error: ErrorInfo | null,
  ): AgentCaptureSpan {
    let finalized: AgentCaptureSpan = {
      span_id: openSpan.spanId,
      parent_span_id: openSpan.parentSpanId,
      trajectory_id: openSpan.trajectoryId,
      name: openSpan.name,
      type: openSpan.type,
      start_time: isoCanonical(openSpan.startTime),
      end_time: isoCanonical(new Date()),
      status,
      error,
      inputs: openSpan.inputs,
      outputs,
      attributes: openSpan.attributes,
      compliance: openSpan.compliance,
      provenance: {
        content_hash: "0".repeat(64),
        parent_content_hash: null,
        schema_version: SCHEMA_VERSION,
      },
    } as AgentCaptureSpan;

    // Redaction runs BEFORE hashing so content_hash covers shipped bytes.
    if (this.options.redactionFilter !== undefined) {
      finalized = this.options.redactionFilter.apply(finalized);
    }

    // content_hash excludes provenance, so the placeholder above is fine.
    const myHash = contentHash(finalized);
    finalized = {
      ...finalized,
      provenance: {
        content_hash: myHash,
        parent_content_hash: null,
        schema_version: SCHEMA_VERSION,
      },
    };

    // Stamp every pending descendant with my hash, then ship them.
    for (const child of openSpan.pending) {
      const stamped: AgentCaptureSpan = {
        ...child,
        provenance: {
          content_hash: child.provenance.content_hash,
          parent_content_hash: myHash,
          schema_version: child.provenance.schema_version ?? SCHEMA_VERSION,
        },
      };
      this.exporter.export(stamped);
    }
    openSpan.pending = [];

    if (openSpan.parent === null) {
      // Root: ship directly.
      this.exporter.export(finalized);
    } else {
      // Non-root: park with parent. Parent will stamp parent_content_hash on close.
      openSpan.parent.pending.push(finalized);
    }
    return finalized;
  }
}
