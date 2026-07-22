/**
 * Local-only diagnostic logging + stable error codes.
 *
 * Mirrors agent_capture._internal.safelog (Python). The ErrorCode catalog
 * and REMEDIATION map are kept in lockstep with the Python implementation
 * so customers see the same ACxxx codes in their logs regardless of which
 * language SDK produced them.
 *
 * Logs go to the standard `console` by default. Vendors that want
 * structured ops telemetry replace `setLogger()` with a function that
 * routes into their stack (datadog, splunk, etc.).
 */

export type LogFn = (level: "debug" | "info" | "warn" | "error", message: string, exc?: unknown) => void;

let _logger: LogFn = (level, message, exc) => {
  const fn =
    level === "error" || level === "warn"
      ? console.error
      : console.log;
  if (exc !== undefined) fn(message, exc);
  else fn(message);
};

/** Replace the SDK logger. Useful for routing into vendor ops stacks. */
export function setLogger(logger: LogFn): void {
  _logger = logger;
}

export function safelog(): LogFn {
  return _logger;
}

// ---- error codes ---------------------------------------------------------

export enum ErrorCode {
  // Span builder / decorator
  AC101 = "AC101", // SpanBuilder.close internal failure
  AC102 = "AC102", // traced() open failed (likely no builder configured)
  AC103 = "AC103", // traced() close-with-error failed
  AC104 = "AC104", // traced() scope cleanup failed

  // Instrumentation
  AC201 = "AC201", // SDK wrapper buildRequestAttrs failed
  AC202 = "AC202", // SDK wrapper open failed
  AC203 = "AC203", // SDK wrapper attachResponse failed
  AC204 = "AC204", // Framework adapter open failed
  AC205 = "AC205", // Framework adapter close failed
  AC206 = "AC206", // Framework adapter close-with-error failed

  // Redaction
  AC301 = "AC301", // Redaction pipeline raised; fell back to over-redaction
  AC302 = "AC302", // Fallback re-validation failed; returning original span

  // Exporter
  AC401 = "AC401", // File exporter write failed
  AC402 = "AC402", // HTTP serialization failed; dropping batch
  AC403 = "AC403", // HTTP permanent failure (4xx); dropping batch
  AC404 = "AC404", // HTTP retries exhausted; dropping batch
  AC405 = "AC405", // Inner exporter raised in queue worker
  AC406 = "AC406", // Critical span dropped (queue saturated past timeout)
  AC407 = "AC407", // Queue full at shutdown
  AC408 = "AC408", // Spool persist failed
  AC409 = "AC409", // Spool replay: file corrupt
  AC410 = "AC410", // Spool replay: export failed
  AC411 = "AC411", // Spool replay: could not delete after success
  AC412 = "AC412", // Shutdown handler: exporter.shutdown raised
  AC413 = "AC413", // Shutdown handler: drain_unshipped raised
}

export const REMEDIATION: Record<ErrorCode, string> = {
  [ErrorCode.AC101]:
    "verify span attributes match the declared SpanType and that the compliance metadata is complete",
  [ErrorCode.AC102]:
    "call configure({ exporter, defaultCompliance }) during process startup before any traced() function runs",
  [ErrorCode.AC103]:
    "see AC101 — close-with-error reuses the same close path",
  [ErrorCode.AC104]:
    "AsyncLocalStorage scope cleanup raised; usually means a tracedScope was crossed by non-context-aware async work",
  [ErrorCode.AC201]:
    "the provider's request shape may have changed; check the installed SDK version",
  [ErrorCode.AC202]: "see AC102",
  [ErrorCode.AC203]:
    "the provider's response shape may have changed; check the installed SDK version",
  [ErrorCode.AC204]:
    "the framework callback fired with arguments the adapter cannot translate; check the framework version",
  [ErrorCode.AC205]: "see AC101",
  [ErrorCode.AC206]: "see AC101",
  [ErrorCode.AC301]:
    "check the customer policy YAML for malformed patterns or strategy references; the span shipped over-redacted",
  [ErrorCode.AC302]:
    "CRITICAL: redaction fallback could not re-validate; original (potentially sensitive) bytes returned. Investigate immediately",
  [ErrorCode.AC401]: "check disk space, filesystem permissions, and the configured path",
  [ErrorCode.AC402]:
    "a span contained a value that JSON.stringify cannot serialize; the batch was dropped",
  [ErrorCode.AC403]:
    "the ledger rejected the batch as malformed; verify SDK version against the ledger ingest contract",
  [ErrorCode.AC404]:
    "the ledger is unreachable or returning persistent 5xx; check ledger health and network connectivity",
  [ErrorCode.AC405]:
    "see AC401/AC404 depending on the inner exporter",
  [ErrorCode.AC406]:
    "the bounded queue saturated and a critical span timed out waiting; increase queue max or batch throughput",
  [ErrorCode.AC407]:
    "shutdown could not enqueue the drain sentinel; queue likely saturated by a stalled worker",
  [ErrorCode.AC408]:
    "could not write the spool file at shutdown; spans were lost. Check disk space and permissions",
  [ErrorCode.AC409]:
    "a spool file is unparseable; left in place for ops to inspect",
  [ErrorCode.AC410]:
    "could not re-export a spool file; left in place to retry next start",
  [ErrorCode.AC411]:
    "could not delete a successfully-replayed spool file; spans may ship twice on next restart",
  [ErrorCode.AC412]: "see the inner exporter's documented codes (AC401, AC404, …)",
  [ErrorCode.AC413]:
    "the custom drain callable raised; unshipped spans were not persisted",
};

/**
 * Log a structured error with a stable code and remediation hint.
 *
 * Format: `[ACxxx] <message> — fix: <remediation>`
 */
export function logError(code: ErrorCode, message: string, exc?: unknown): void {
  const remediation = REMEDIATION[code] ?? "";
  const suffix = remediation ? ` — fix: ${remediation}` : "";
  _logger("error", `[${code}] ${message}${suffix}`, exc);
}
