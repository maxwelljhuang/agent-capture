/**
 * W3C Trace Context inject/extract for cross-process linking.
 *
 * Mirrors agent_capture.context.w3c. Format:
 *   traceparent: {version}-{trace_id}-{span_id}-{flags}
 *     version="00", trace_id=32 hex, span_id=16 hex, flags=2 hex.
 */

import { currentParent } from "./propagation.js";

export const TRACEPARENT_HEADER = "traceparent";
export const TRACESTATE_HEADER = "tracestate";

const TRACEPARENT_PATTERN =
  /^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/;

export interface RemoteParent {
  trajectoryId: string;
  spanId: string;
  sampled: boolean;
}

export function inject(options: { sampled?: boolean } = {}): Record<string, string> {
  const parent = currentParent();
  if (parent === null) return {};
  const flags = options.sampled === false ? "00" : "01";
  return {
    [TRACEPARENT_HEADER]: `00-${parent.trajectoryId}-${parent.spanId}-${flags}`,
  };
}

export function extract(
  headers: Record<string, string> | Headers,
): RemoteParent | null {
  const raw = caseInsensitiveGet(headers, TRACEPARENT_HEADER);
  if (raw === null) return null;
  const match = TRACEPARENT_PATTERN.exec(raw.trim());
  if (match === null) return null;
  const [, version, traceId, spanId, flagsHex] = match;
  if (version === "ff") return null; // reserved per spec
  const flags = parseInt(flagsHex!, 16);
  return {
    trajectoryId: traceId!,
    spanId: spanId!,
    sampled: (flags & 0x01) !== 0,
  };
}

function caseInsensitiveGet(
  headers: Record<string, string> | Headers,
  name: string,
): string | null {
  if (headers instanceof Headers) {
    return headers.get(name);
  }
  if (name in headers) {
    const value = headers[name];
    return value === undefined ? null : value;
  }
  const target = name.toLowerCase();
  for (const [k, v] of Object.entries(headers)) {
    if (k.toLowerCase() === target) return v;
  }
  return null;
}
