"""Programmatic and environment-driven configuration.

Two configuration surfaces:

1. The vendor's developer calls :func:`configure` at process start to wire
   up exporters, set the default compliance metadata, register a redaction
   policy, and so on.
2. Operators can override most settings via ``AGENT_CAPTURE_*`` environment
   variables so deployments don't require code changes.

Both feed into a single immutable ``Settings`` object that the rest of the
SDK reads from. The Week 2 implementation will fill in the runtime fields;
this Week 1 stub defines the shape so downstream modules can import the
type names without a circular dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_capture._internal.runtime import set_default_builder
from agent_capture.exporter.base import SpanExporter
from agent_capture.redaction.filter import RedactionFilter
from agent_capture.schema.compliance import ComplianceMetadata
from agent_capture.span.builder import SpanBuilder


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for the capture engine."""

    default_compliance: ComplianceMetadata | None = None
    """Compliance metadata applied to every span unless overridden at construction."""

    queue_max_size: int = 10_000
    """Maximum number of in-flight spans before drops begin. See exporter/queue.py."""

    batch_max_size: int = 100
    """Maximum spans per export batch."""

    batch_max_wait_ms: int = 1_000
    """Maximum time a span waits in the queue before being flushed."""

    spool_directory: str = "~/.agent-capture/spool"
    """Where the exporter persists in-flight spans on shutdown."""

    safe_mode: bool = True
    """When True, all public entry points swallow exceptions. Must stay True in prod."""

    extra: dict[str, str] = field(default_factory=dict)
    """Vendor-defined extension keys. Reserved for opaque downstream use."""


_settings: Settings | None = None


def configure(
    *,
    exporter: SpanExporter,
    default_compliance: ComplianceMetadata | None = None,
    redaction_filter: RedactionFilter | None = None,
    flush_on_root_close: bool = False,
    **settings_kwargs: object,
) -> SpanBuilder:
    """Wire up the capture engine and register a process-wide default builder.

    After this call, ``@traced`` and ``with traced(...)`` know where to send
    spans. The returned :class:`SpanBuilder` can also be used directly for
    callers that prefer not to rely on the global.

    Args:
        exporter: The destination spans flow to.
        default_compliance: Compliance metadata applied to every span unless
            overridden at construction. Required if any caller will open a
            span without passing ``compliance=...`` explicitly.
        redaction_filter: Optional in-process redaction filter. Runs inside
            :meth:`SpanBuilder.close` before ``content_hash`` is computed so
            hashes cover only the post-redaction bytes. Strongly recommended
            for production; omit only when the customer has explicitly
            classified every captured field as non-sensitive.
        flush_on_root_close: Serverless mode. When ``True``, the exporter is
            flushed synchronously each time a trajectory's root span closes, so
            spans aren't stranded in the async queue after a Cloud Run / Lambda
            response returns and CPU is throttled. Adds the ledger round-trip to
            request latency; enable on serverless, leave off for long-running
            processes. See ``docs/integration-guide.md``.
        **settings_kwargs: Forwarded to :class:`Settings`.
    """
    global _settings
    _settings = Settings(
        default_compliance=default_compliance,
        **{k: v for k, v in settings_kwargs.items() if k in Settings.__dataclass_fields__},  # type: ignore[arg-type]
    )
    builder = SpanBuilder(
        exporter,
        default_compliance=default_compliance,
        redaction_filter=redaction_filter,
        flush_on_root_close=flush_on_root_close,
    )
    set_default_builder(builder)
    return builder


def current_settings() -> Settings:
    """Return the active Settings, or a default-initialized instance."""
    return _settings or Settings()
