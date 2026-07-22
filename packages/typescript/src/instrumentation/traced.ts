/**
 * Manual instrumentation primitives.
 *
 *   const wrapped = traced({ type, name }, async (...args) => ...)
 *   await tracedScope({ type, name }, async () => { ... })
 *
 * Two APIs because TypeScript decorators only attach to classes; for
 * arbitrary functions we use the wrapper pattern. tracedScope is the
 * context-manager equivalent — opens a span, runs the body inside its
 * span_scope, closes the span on return or throw.
 *
 * Mirrors agent_capture.instrumentation.decorator.traced (which combines
 * decorator + CM via __call__/__enter__/__exit__).
 */

import { spanScope } from "../context/propagation.js";
import { defaultBuilder } from "../internal/runtime.js";
import { ErrorCode, logError, safelog } from "../internal/safelog.js";
import type {
  ComplianceMetadata,
  ErrorInfo,
  Attributes as TypedAttributes,
} from "../schema/span.js";
import type { OpenSpan, SpanType } from "../span/types.js";
import type { SpanBuilder } from "../span/builder.js";

export interface TracedOptions {
  type: SpanType;
  name?: string;
  attributes?: TypedAttributes;
  compliance?: ComplianceMetadata;
  builder?: SpanBuilder;
}

/**
 * Wrap a function so each call produces a span. Sync and async are
 * handled uniformly via Promise.resolve; if the underlying function is
 * sync, the wrapper returns its value synchronously after the span is
 * closed.
 */
export function traced<A extends unknown[], R>(
  options: TracedOptions,
  fn: (...args: A) => R | Promise<R>,
): (...args: A) => R | Promise<R> {
  return (...args: A): R | Promise<R> => {
    const builder = options.builder ?? defaultBuilder();
    if (builder === null) {
      safelog()(
        "debug",
        "traced(): no builder configured; passing through",
      );
      return fn(...args);
    }
    let open: OpenSpan;
    try {
      const openOpts: Parameters<SpanBuilder["open"]>[0] = {
        name: options.name ?? fn.name ?? options.type,
        type: options.type,
        attributes: options.attributes ?? defaultAttributesFor(options.type, options.name ?? fn.name ?? options.type),
        inputs: argsAsInputs(args),
      };
      if (options.compliance !== undefined) openOpts.compliance = options.compliance;
      open = builder.open(openOpts);
    } catch (exc) {
      logError(ErrorCode.AC102, "traced(): open failed", exc);
      return fn(...args);
    }

    const result = spanScope(open, () => {
      let ret: R | Promise<R>;
      try {
        ret = fn(...args);
      } catch (exc) {
        closeWithError(builder, open, exc);
        throw exc;
      }
      if (isPromiseLike(ret)) {
        return (ret as Promise<R>).then(
          (value) => {
            builder.close(open, { outputs: safeOutputs(value) });
            return value;
          },
          (exc) => {
            closeWithError(builder, open, exc);
            throw exc;
          },
        );
      }
      builder.close(open, { outputs: safeOutputs(ret) });
      return ret;
    });
    return result;
  };
}

/**
 * Context-manager equivalent. Opens a span, runs `body` inside its scope,
 * closes the span on return or throw.
 */
export async function tracedScope<R>(
  options: TracedOptions,
  body: () => R | Promise<R>,
): Promise<R> {
  const builder = options.builder ?? defaultBuilder();
  if (builder === null) {
    return body();
  }
  let open: OpenSpan;
  try {
    const openOpts: Parameters<SpanBuilder["open"]>[0] = {
      name: options.name ?? options.type,
      type: options.type,
      attributes: options.attributes ?? defaultAttributesFor(options.type, options.name ?? options.type),
    };
    if (options.compliance !== undefined) openOpts.compliance = options.compliance;
    open = builder.open(openOpts);
  } catch (exc) {
    logError(ErrorCode.AC102, "tracedScope(): open failed", exc);
    return body();
  }
  return spanScope(open, async () => {
    try {
      const value = await body();
      builder.close(open, { outputs: safeOutputs(value) });
      return value;
    } catch (exc) {
      closeWithError(builder, open, exc);
      throw exc;
    }
  });
}

// ---- helpers ------------------------------------------------------------

function closeWithError(builder: SpanBuilder, open: OpenSpan, exc: unknown): void {
  try {
    const info: ErrorInfo = exc instanceof Error
      ? { error_type: exc.constructor.name, message: exc.message, stack_trace: exc.stack ?? null }
      : { error_type: typeof exc, message: String(exc), stack_trace: null };
    builder.close(open, { status: "error", error: info });
  } catch (closeExc) {
    logError(ErrorCode.AC103, "traced(): close-with-error failed", closeExc);
  }
}

function argsAsInputs(args: unknown[]): unknown {
  if (args.length === 0) return null;
  return args.map(safeOutputs);
}

function safeOutputs(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) return value.map(safeOutputs);
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = safeOutputs(v);
    }
    return out;
  }
  try {
    return String(value);
  } catch {
    return "<unrepresentable>";
  }
}

function isPromiseLike(v: unknown): v is PromiseLike<unknown> {
  return (
    v !== null &&
    v !== undefined &&
    typeof (v as { then?: unknown }).then === "function"
  );
}

function defaultAttributesFor(type: SpanType, name: string): TypedAttributes {
  // Placeholder typed-attributes — callers passing { attributes } override.
  // The fields here are deliberately uninformative so callers see what is
  // missing and supply real values.
  switch (type) {
    case "model_call":
      return { kind: "model_call", model_name: name, model_version: null, provider: "unknown", prompt_template_id: null, prompt_template_version: null, temperature: null, max_tokens: null, input_tokens: null, output_tokens: null, total_tokens: null };
    case "tool_call":
      return { kind: "tool_call", tool_name: name, tool_schema_version: null, arguments: null, return_value: null };
    case "retrieval":
      return { kind: "retrieval", source_identifier: name, query: null, returned_document_ids: [], relevance_scores: [] };
    case "planner_step":
      return { kind: "planner_step", decision_rationale: null, options_considered: [], chosen_option: null };
    case "sub_agent_invocation":
      return { kind: "sub_agent_invocation", sub_agent_identity: name, sub_agent_version: null };
    case "human_approval":
      return { kind: "human_approval", approver_identity: "unknown", approver_role: "unknown", decision: "approved", decision_timestamp: "1970-01-01T00:00:00.000000Z", artifact_reviewed: name, signature: null };
    case "side_effect":
      return { kind: "side_effect", action_type: name, target_system: "unknown", payload_summary: null, idempotency_key: null, success: true };
    case "policy_check":
      return { kind: "policy_check", policy_name: name, policy_version: "unknown", result: "not_applicable", rule_details: null };
  }
}
