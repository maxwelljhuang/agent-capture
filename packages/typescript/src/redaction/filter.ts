/**
 * Two-pass redaction filter — schema-aware then pattern-based.
 * Mirrors agent_capture.redaction.filter.
 *
 * Wired into SpanBuilder.close() between span construction and content_hash
 * computation so hashes cover the post-redaction bytes.
 *
 * Fail-safe: any internal exception triggers an over-redaction fallback
 * that replaces every primitive leaf with [REDACTED:fallback]. The span
 * still ships (agent must always win) but with no possibility of leaking
 * the original. The only path where original bytes can ship is when even
 * the fallback re-validation fails (AC302) — investigate every hit.
 */

import { ErrorCode, logError } from "../internal/safelog.js";
import type { AgentCaptureSpan } from "../schema/span.js";

import { PatternRedactor } from "./pattern.js";
import { DEFAULT_RECOGNIZERS, type Recognizer } from "./patternsFinance.js";
import type { Policy } from "./policy.js";
import { FullRedaction, HmacFingerprint, type RedactionStrategy } from "./strategies.js";

/** Default env var holding the HMAC key used to fingerprint subject_id. */
export const DEFAULT_SUBJECT_KEY_ENV = "AGENT_CAPTURE_HMAC_KEY";

export interface RedactionFilterOptions {
  policy: Policy;
  recognizers?: ReadonlyArray<Recognizer>;
  /** Env var for the key used to fingerprint compliance.subject_id (floor). */
  subjectKeyEnv?: string;
}

const STRUCTURAL_KEYS = new Set<string>(["kind"]);

export class RedactionFilter {
  private readonly schema: SchemaAwareRedactor;
  private readonly pattern: PatternRedactor;
  private readonly subjectFp: HmacFingerprint;

  constructor(public readonly options: RedactionFilterOptions) {
    this.schema = new SchemaAwareRedactor(options.policy);
    this.pattern = new PatternRedactor({
      strategyFor: (ft) => options.policy.strategyForPattern(ft),
      recognizers: options.recognizers ?? DEFAULT_RECOGNIZERS,
    });
    this.subjectFp = new HmacFingerprint({
      keyEnv: options.subjectKeyEnv ?? DEFAULT_SUBJECT_KEY_ENV,
    });
  }

  apply(span: AgentCaptureSpan): AgentCaptureSpan {
    try {
      const out: AgentCaptureSpan = { ...span };
      out.inputs = this.redactValue(span.inputs);
      out.outputs = this.redactValue(span.outputs);
      out.attributes = this.redactValue(span.attributes) as AgentCaptureSpan["attributes"];
      const fpSubject = this.fingerprintSubject(span.compliance.subject_id);
      // Only set subject_id when defined (exactOptionalPropertyTypes).
      out.compliance = fpSubject === undefined ? span.compliance : { ...span.compliance, subject_id: fpSubject };
      return out;
    } catch (exc) {
      logError(
        ErrorCode.AC301,
        "RedactionFilter.apply failed; falling back to FullRedaction sweep",
        exc,
      );
      return this.fallback(span);
    }
  }

  /** HMAC-fingerprint subject_id; full-redact if no key. Idempotent. */
  private fingerprintSubject(subjectId: string | null | undefined): string | null | undefined {
    if (subjectId === null || subjectId === undefined) return subjectId;
    const text = String(subjectId);
    if (text.startsWith("[FP:") || text.startsWith("[REDACTED:")) return subjectId;
    try {
      return this.subjectFp.redact(text, { fieldType: "subject_id" });
    } catch {
      return new FullRedaction().redact(text, { fieldType: "subject_id" });
    }
  }

  private redactValue(value: unknown): unknown {
    const post = this.schema.redact(value);
    return walkStrings(post, (s) => this.pattern.redact(s));
  }

  private fallback(span: AgentCaptureSpan): AgentCaptureSpan {
    const full = new FullRedaction();
    try {
      const subjectId = span.compliance.subject_id;
      return {
        ...span,
        inputs: fullRedactTree(span.inputs, full),
        outputs: fullRedactTree(span.outputs, full),
        attributes: fullRedactTree(span.attributes, full) as AgentCaptureSpan["attributes"],
        compliance:
          subjectId === null || subjectId === undefined
            ? span.compliance
            : { ...span.compliance, subject_id: full.redact(String(subjectId), { fieldType: "subject_id" }) },
      };
    } catch (exc) {
      logError(
        ErrorCode.AC302,
        "RedactionFilter fallback failed; returning ORIGINAL un-redacted span",
        exc,
      );
      return span;
    }
  }
}

class SchemaAwareRedactor {
  constructor(private readonly policy: Policy) {}

  redact(value: unknown): unknown {
    return this.walk(value, null, null);
  }

  private walk(
    value: unknown,
    inherited: RedactionStrategy | null,
    currentFieldType: string | null,
  ): unknown {
    if (Array.isArray(value)) {
      return value.map((v) => this.walk(v, inherited, currentFieldType));
    }
    if (value !== null && typeof value === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
        out[k] = this.walkDictValue(k, v, inherited);
      }
      return out;
    }
    if (inherited === null || value === null || value === undefined) return value;
    return inherited.redact(String(value), { fieldType: currentFieldType ?? "field" });
  }

  private walkDictValue(
    key: string,
    value: unknown,
    inherited: RedactionStrategy | null,
  ): unknown {
    const strategy = this.policy.strategyForField(key);
    if (strategy === null) return this.walk(value, inherited, null);
    return this.walk(value, strategy, key.toLowerCase());
  }
}

function walkStrings(value: unknown, fn: (s: string) => string): unknown {
  if (typeof value === "string") return fn(value);
  if (Array.isArray(value)) return value.map((v) => walkStrings(v, fn));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = walkStrings(v, fn);
    }
    return out;
  }
  return value;
}

function fullRedactTree(value: unknown, strategy: FullRedaction): unknown {
  if (Array.isArray(value)) return value.map((v) => fullRedactTree(v, strategy));
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out[k] = STRUCTURAL_KEYS.has(k) ? v : fullRedactTree(v, strategy);
    }
    return out;
  }
  if (value === null || value === undefined || typeof value === "boolean") return value;
  return strategy.redact(String(value), { fieldType: "fallback" });
}
