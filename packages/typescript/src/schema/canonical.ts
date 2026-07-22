/**
 * Canonical span serialization for hashing.
 *
 * Must produce byte-identical output to the Python implementation at
 * packages/python/src/agent_capture/schema/canonical.py for any given
 * logical span. The provenance hash chain depends on this. Any change
 * here is a schema-breaking change and must bump SCHEMA_VERSION in
 * lockstep with the Python side.
 *
 * Rules:
 *
 * 1. Object keys sorted lexicographically (UTF-8 codepoint order).
 * 2. No insignificant whitespace; separators are "," and ":".
 * 3. UTF-8 encoding of the JSON string is the byte input to SHA-256.
 * 4. Date / datetime values rendered as
 *    "YYYY-MM-DDTHH:MM:SS.ffffffZ" (microsecond precision, UTC, "Z").
 *    JavaScript's Date has millisecond precision; the last three
 *    microsecond digits are emitted as "000" so the output exactly
 *    matches a Python datetime with millis truncated. A pre-formatted
 *    string matching the pattern is passed through verbatim.
 * 5. The "provenance" key is excluded from the hash input by default
 *    (a span cannot hash over its own hash).
 * 6. null values preserved — presence vs. absence of a key matters.
 * 7. Floats: standard JSON.stringify shortest round-trip. NaN/Infinity
 *    are forbidden.
 */

import { createHash } from "node:crypto";

const DATETIME_RE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;

export interface CanonicalOptions {
  /** When true (default), strip the `provenance` key before serialization. */
  excludeProvenance?: boolean;
}

/**
 * Recursively normalize a value into JSON-canonicalizable primitives.
 * Anything not handled here passes through to JSON.stringify, which
 * matches Python's json module for primitive numbers and strings.
 */
function normalize(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("Canonical form forbids NaN and Infinity.");
    }
    return value;
  }
  if (typeof value === "boolean" || typeof value === "string") {
    return value;
  }
  if (value instanceof Date) {
    return formatDate(value);
  }
  if (Array.isArray(value)) {
    return value.map(normalize);
  }
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    const keys = Object.keys(value as Record<string, unknown>).sort();
    for (const k of keys) {
      out[k] = normalize((value as Record<string, unknown>)[k]);
    }
    return out;
  }
  return value;
}

function formatDate(d: Date): string {
  // YYYY-MM-DDTHH:MM:SS.ffffffZ — pad millis (3 digits) with "000".
  const pad = (n: number, width: number) => String(n).padStart(width, "0");
  const yyyy = pad(d.getUTCFullYear(), 4);
  const mm = pad(d.getUTCMonth() + 1, 2);
  const dd = pad(d.getUTCDate(), 2);
  const hh = pad(d.getUTCHours(), 2);
  const mi = pad(d.getUTCMinutes(), 2);
  const ss = pad(d.getUTCSeconds(), 2);
  const ms = pad(d.getUTCMilliseconds(), 3);
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}:${ss}.${ms}000Z`;
}

/**
 * Stable JSON.stringify with sorted keys, no whitespace, no NaN.
 * Used by `canonicalBytes`. Operates on already-normalized input.
 */
function stableStringify(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error("Canonical form forbids NaN and Infinity.");
    }
    // JSON.stringify produces shortest round-trip for finite numbers.
    return JSON.stringify(value);
  }
  if (typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return "[" + value.map(stableStringify).join(",") + "]";
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    // keys are already sorted by `normalize`, but guard anyway.
    entries.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return (
      "{" +
      entries.map(([k, v]) => JSON.stringify(k) + ":" + stableStringify(v)).join(",") +
      "}"
    );
  }
  throw new Error(`Cannot canonicalize value of type ${typeof value}`);
}

/**
 * Validate that any datetime-shaped string in the input matches the
 * canonical pattern. Used as a guard so accidental ISO 8601 strings
 * with timezone offsets or wrong precision don't silently produce
 * the wrong hash.
 */
function assertDatetimeShapes(value: unknown): void {
  if (typeof value === "string") {
    // Heuristic: if it looks like an ISO date but doesn't match canonical
    // form, complain.
    if (
      /^\d{4}-\d{2}-\d{2}T/.test(value) &&
      !DATETIME_RE.test(value)
    ) {
      throw new Error(
        `Datetime string ${JSON.stringify(value)} is not in canonical form ` +
          `(YYYY-MM-DDTHH:MM:SS.ffffffZ). Pass a Date or pre-format manually.`,
      );
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const v of value) assertDatetimeShapes(v);
    return;
  }
  if (value && typeof value === "object") {
    for (const v of Object.values(value as Record<string, unknown>)) {
      assertDatetimeShapes(v);
    }
  }
}

/**
 * Return the canonical UTF-8 byte string for `value`.
 *
 * @param value     Any JSON-shaped object. Pydantic-dumped span objects work.
 * @param options.excludeProvenance  When true (default), strip the top-level
 *                  `provenance` key before serialization.
 */
export function canonicalBytes(
  value: unknown,
  options: CanonicalOptions = {},
): Uint8Array {
  const { excludeProvenance = true } = options;
  let working: unknown = value;
  if (
    excludeProvenance &&
    working &&
    typeof working === "object" &&
    !Array.isArray(working) &&
    "provenance" in (working as Record<string, unknown>)
  ) {
    const { provenance: _drop, ...rest } = working as Record<string, unknown>;
    working = rest;
  }
  assertDatetimeShapes(working);
  const normalized = normalize(working);
  const json = stableStringify(normalized);
  return new TextEncoder().encode(json);
}

/** String form of `canonicalBytes`; useful for debugging and golden fixtures. */
export function canonicalJson(
  value: unknown,
  options: CanonicalOptions = {},
): string {
  return new TextDecoder("utf-8").decode(canonicalBytes(value, options));
}

/** Hex-encoded SHA-256 of the canonical form. */
export function contentHash(value: unknown): string {
  const bytes = canonicalBytes(value, { excludeProvenance: true });
  return createHash("sha256").update(bytes).digest("hex");
}
