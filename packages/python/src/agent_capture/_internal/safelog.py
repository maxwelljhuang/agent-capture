"""Local-only diagnostic logging + stable error codes.

Failures inside the capture engine must never propagate to the host agent.
When something goes wrong, we log to a vendor-observable destination via this
module — never via ``print``, ``warnings.warn``, or by re-raising.

Default destination is a child logger of the standard ``logging`` hierarchy
at the name ``agent_capture``. Vendors who want machine-parseable telemetry
can attach a handler that writes to a file the ops team monitors.

Every error log carries a stable :class:`ErrorCode` so customers can grep
their log streams for ``AC401`` and know "file exporter write failed"
regardless of how the surrounding message text evolves. The
:func:`log_error` helper formats the standard line::

    [AC401] FileExporter.export failed: disk full — fix: check disk space, …
"""

from __future__ import annotations

import logging
from enum import StrEnum

_logger = logging.getLogger("agent_capture")
_logger.addHandler(logging.NullHandler())


def safelog() -> logging.Logger:
    """Return the SDK's diagnostic logger.

    Always returns the same logger instance, so handlers attached by the
    vendor at process start remain effective.
    """
    return _logger


# ---- error codes ---------------------------------------------------------


class ErrorCode(StrEnum):
    """Stable identifiers for every safelog().error() site in the SDK.

    The code is load-bearing; message text may evolve, but a given
    subsystem failure always logs the same code. The numbering is:

    - ``AC1xx`` — span builder, manual decorator
    - ``AC2xx`` — instrumentation (SDK wrappers, framework adapters)
    - ``AC3xx`` — redaction
    - ``AC4xx`` — exporter (file, queue, HTTP, shutdown / spool)
    - ``AC5xx`` — enforcement gate (recorder-side hook + engine client)
    """

    # --- Span builder / decorator -----------------------------------
    AC101 = "AC101"  # SpanBuilder.close internal failure
    AC102 = "AC102"  # @traced open failed (likely no builder configured)
    AC103 = "AC103"  # @traced close-with-error failed
    AC104 = "AC104"  # @traced.__exit__ span_scope failed

    # --- Instrumentation --------------------------------------------
    AC201 = "AC201"  # SDK wrapper build_request_attrs failed
    AC202 = "AC202"  # SDK wrapper open failed
    AC203 = "AC203"  # SDK wrapper attach_response failed
    AC204 = "AC204"  # Framework adapter open failed
    AC205 = "AC205"  # Framework adapter close failed
    AC206 = "AC206"  # Framework adapter close-with-error failed

    # --- Redaction --------------------------------------------------
    AC301 = "AC301"  # Redaction pipeline raised; fell back to over-redaction
    AC302 = "AC302"  # Fallback re-validation failed; returning original span

    # --- Exporter ---------------------------------------------------
    AC401 = "AC401"  # File exporter write failed
    AC402 = "AC402"  # HTTP serialization failed; dropping batch
    AC403 = "AC403"  # HTTP permanent failure (4xx); dropping batch
    AC404 = "AC404"  # HTTP retries exhausted; dropping batch
    AC405 = "AC405"  # Inner exporter raised in queue worker
    AC406 = "AC406"  # Critical span dropped (queue saturated past timeout)
    AC407 = "AC407"  # Queue full at shutdown
    AC408 = "AC408"  # Spool persist failed
    AC409 = "AC409"  # Spool replay: file corrupt
    AC410 = "AC410"  # Spool replay: export failed
    AC411 = "AC411"  # Spool replay: could not delete after success
    AC412 = "AC412"  # Shutdown handler: exporter.shutdown raised
    AC413 = "AC413"  # Shutdown handler: drain_unshipped raised
    AC414 = "AC414"  # Tenant routing: no exporter for tenant; dropping span
    AC415 = "AC415"  # Tenant routing: inner exporter or factory raised
    AC416 = "AC416"  # flush_on_root_close: exporter.flush raised

    # --- Enforcement gate -------------------------------------------
    AC501 = "AC501"  # Gate evaluate raised internally; fell back to fail-open
    AC502 = "AC502"  # Verdict service unreachable / timed out
    AC503 = "AC503"  # Hold resolution channel failed
    AC504 = "AC504"  # Emit-verdict (policy_check) span failed


REMEDIATION: dict[ErrorCode, str] = {
    ErrorCode.AC101: (
        "verify span attributes match the declared SpanType and that the compliance metadata is complete"
    ),
    ErrorCode.AC102: (
        "call agent_capture.configure(exporter=..., default_compliance=...) "
        "during process startup before any @traced function runs"
    ),
    ErrorCode.AC103: "see AC101 — close-with-error reuses the same close path",
    ErrorCode.AC104: (
        "internal contextvars Token mismatch — usually means a span_scope "
        "context manager was crossed by code that nests via threads without "
        "bind_context()"
    ),
    ErrorCode.AC201: (
        "the provider's request kwargs shape may have changed; check the "
        "installed SDK version against the wrapper's expectations"
    ),
    ErrorCode.AC202: "see AC102",
    ErrorCode.AC203: (
        "the provider's response object shape may have changed; check the "
        "installed SDK version against the wrapper's expectations"
    ),
    ErrorCode.AC204: (
        "the framework callback fired with arguments the adapter cannot "
        "translate; check the langchain-core version against the adapter"
    ),
    ErrorCode.AC205: "see AC101",
    ErrorCode.AC206: "see AC101",
    ErrorCode.AC301: (
        "check the customer policy YAML for malformed patterns or strategy "
        "references; the span shipped over-redacted as a safe fallback"
    ),
    ErrorCode.AC302: (
        "CRITICAL: redaction fallback could not re-validate the over-redacted "
        "span; original (potentially sensitive) bytes returned. Investigate "
        "the schema mismatch immediately"
    ),
    ErrorCode.AC401: ("check disk space, filesystem permissions, and the configured path"),
    ErrorCode.AC402: ("a span contained a value that Pydantic/JSON cannot serialize; this batch was dropped"),
    ErrorCode.AC403: (
        "the ledger rejected the batch as malformed; verify SDK version against the ledger ingest contract"
    ),
    ErrorCode.AC404: (
        "the ledger is unreachable or returning persistent 5xx; check ledger health and network connectivity"
    ),
    ErrorCode.AC405: "see AC401/AC404 depending on the inner exporter",
    ErrorCode.AC406: (
        "the bounded queue saturated and a critical (human_approval or "
        "side_effect) span timed out waiting; increase max_size on the "
        "queue or batch throughput on the inner exporter"
    ),
    ErrorCode.AC407: ("shutdown could not enqueue the drain sentinel; queue likely saturated by a stalled worker"),
    ErrorCode.AC408: (
        "could not write the spool file at shutdown; spans were lost. "
        "Check disk space and permissions on the spool directory"
    ),
    ErrorCode.AC409: ("a spool file is unparseable; left in place for ops to inspect"),
    ErrorCode.AC410: ("could not re-export a spool file; left in place to retry next start"),
    ErrorCode.AC411: (
        "could not delete a successfully-replayed spool file; spans may "
        "ship twice on next restart unless removed manually"
    ),
    ErrorCode.AC412: "see the inner exporter's documented codes (AC401, AC404, …)",
    ErrorCode.AC413: ("the custom drain callable raised; unshipped spans were not persisted"),
    ErrorCode.AC414: (
        "no ingest token/exporter is configured for this end_customer_id; add the "
        "tenant to the TenantRoutingExporter's token map or token_provider"
    ),
    ErrorCode.AC415: "see AC404/AC405 — the per-tenant inner exporter (or its factory) failed",
    ErrorCode.AC416: (
        "the configured exporter's flush() raised during flush_on_root_close; the trajectory "
        "may not have shipped synchronously — check the exporter/ledger health"
    ),
    ErrorCode.AC501: (
        "the registered enforcement gate raised internally; the action was allowed "
        "(fail-open) since a gate bug is not an explicit fail-closed decision. "
        "Investigate the gate client; check the agent-capture-enforcement version"
    ),
    ErrorCode.AC502: (
        "the enforcement verdict service was unreachable or exceeded its timeout; "
        "the per-rule failure mode (fail-open / fail-to-human / fail-closed) was "
        "applied from the local fallback table. Check verdict-service health"
    ),
    ErrorCode.AC503: (
        "could not reach the hold-resolution endpoint while a fail-to-human review "
        "was pending; check enforcement engine connectivity"
    ),
    ErrorCode.AC504: (
        "could not emit the policy_check verdict span; the enforcement decision "
        "still applied but is not recorded as a span. Check builder configuration"
    ),
}


def log_error(
    code: ErrorCode,
    message: str,
    /,
    *args: object,
    exc_info: bool = True,
) -> None:
    """Log a structured error with a stable code and remediation hint.

    Formats: ``[ACxxx] <message> — fix: <remediation>``.

    Args:
        code: An :class:`ErrorCode` value identifying the failure site.
        message: A printf-style format string. ``%s`` etc. expand against
            ``args``.
        args: Format arguments for ``message``.
        exc_info: Whether to attach the current exception traceback. The
            default (``True``) is right for ``except`` blocks; pass
            ``False`` for diagnostic-only logs.
    """
    text = message % args if args else message
    remediation = REMEDIATION.get(code, "")
    suffix = f" — fix: {remediation}" if remediation else ""
    safelog().error(f"[{code.value}] {text}{suffix}", exc_info=exc_info)
