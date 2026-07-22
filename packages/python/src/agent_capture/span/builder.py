"""Span builder — constructs and finalizes spans across their lifecycle.

Lifecycle:

1. ``builder.open(...)`` returns a mutable :class:`OpenSpan`. The caller
   wraps its work in :func:`~agent_capture.context.propagation.span_scope`
   so the OpenSpan becomes the current parent for any inner spans.
2. Work happens. Inner spans open and close, registering themselves as
   pending children of their parent.
3. ``builder.close(open_span, outputs=...)`` finalizes the span: validates
   it against the Pydantic schema, computes its ``content_hash`` over the
   canonical serialization, and decides what to ship.

Hash-chain ordering (architecture §4.4): each span's ``content_hash`` is
SHA-256 of its own canonical content (provenance excluded). ``parent_content_hash``
is the parent's final ``content_hash``. Because parent's hash isn't known
until parent closes (parent closes *after* its children), closing children
**buffer** with their parent. When the parent closes, it stamps every
pending descendant with its own ``content_hash`` and ships the whole
subtree to the exporter in leaf-first order. The root span ships last.

This is the only correct interpretation under the architecture's "compute
provenance as the last step before export" rule.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.context.propagation import current_parent
from agent_capture.errors import SchemaViolation
from agent_capture.exporter.base import SpanExporter
from agent_capture.redaction.filter import RedactionFilter
from agent_capture.schema import (
    ComplianceMetadata,
    ErrorInfo,
    ProvenanceFields,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.types import TypedAttributes


def _new_trajectory_id() -> str:
    """Generate an OTel-compatible 128-bit trace id as 32 hex chars."""
    return secrets.token_hex(16)


def _new_span_id() -> str:
    """Generate an OTel-compatible 64-bit span id as 16 hex chars."""
    return secrets.token_hex(8)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class OpenSpan:
    """Mutable in-progress span state.

    Held by the span builder between ``open(...)`` and ``close(...)``. Lives
    in the contextvar so inner spans can locate their parent.
    """

    span_id: str
    parent_span_id: str | None
    trajectory_id: str
    name: str
    type: SpanType
    start_time: datetime
    attributes: TypedAttributes
    compliance: ComplianceMetadata
    inputs: Any | None = None
    _parent: OpenSpan | None = field(default=None, repr=False)
    _pending: list[Span] = field(default_factory=list, repr=False)


class SpanBuilder:
    """Factory for opening and closing spans.

    A single builder is shared across the process. Thread- and async-safe
    because state lives in the contextvar (for parent tracking) or in the
    exporter (for queueing); the builder itself is stateless aside from
    its references to the exporter and default compliance metadata.
    """

    def __init__(
        self,
        exporter: SpanExporter,
        *,
        default_compliance: ComplianceMetadata | None = None,
        redaction_filter: RedactionFilter | None = None,
        flush_on_root_close: bool = False,
    ) -> None:
        self._exporter = exporter
        self._default_compliance = default_compliance
        self._redaction_filter = redaction_filter
        # Serverless mode: synchronously flush the exporter when a trajectory's
        # root span closes, so spans aren't stranded in the async queue after a
        # Cloud Run / Lambda response returns and CPU is throttled. See #64.
        self._flush_on_root_close = flush_on_root_close

    def open(
        self,
        *,
        name: str,
        type: SpanType,
        attributes: TypedAttributes,
        compliance: ComplianceMetadata | None = None,
        inputs: Any | None = None,
        parent: OpenSpan | None = None,
    ) -> OpenSpan:
        """Open a new span.

        Parent resolution order:

        1. Explicit ``parent=`` argument (used by framework adapters that
           wire parent-child by their own run id rather than via the
           ``contextvars`` parent pointer).
        2. Otherwise, :func:`agent_capture.context.current_parent`.

        Raises:
            SchemaViolation: If no compliance metadata is available (neither
                passed in nor configured as a default). This is *internal*
                — callers wrap with try/except per the never-raises rule.
        """
        effective_compliance = compliance or self._default_compliance
        if effective_compliance is None:
            raise SchemaViolation(
                "No compliance metadata available. Pass compliance=... or configure() a default before opening a span."
            )

        if parent is None:
            parent = current_parent()
        if parent is None:
            trajectory_id = _new_trajectory_id()
            span_id = trajectory_id[:16]
            parent_span_id = None
        else:
            trajectory_id = parent.trajectory_id
            span_id = _new_span_id()
            parent_span_id = parent.span_id

        return OpenSpan(
            span_id=span_id,
            parent_span_id=parent_span_id,
            trajectory_id=trajectory_id,
            name=name,
            type=type,
            start_time=_now_utc(),
            attributes=attributes,
            compliance=effective_compliance,
            inputs=inputs,
            _parent=parent,
        )

    def close(
        self,
        open_span: OpenSpan,
        *,
        outputs: Any | None = None,
        status: SpanStatus = SpanStatus.OK,
        error: ErrorInfo | None = None,
    ) -> Span | None:
        """Finalize the span, compute its hash, and ship the subtree if root.

        Returns the finalized :class:`Span` for callers that want to inspect
        it (tests, etc.). On internal failure, logs to safelog and returns
        ``None`` — never raises into the caller.
        """
        try:
            return self._close_impl(open_span, outputs=outputs, status=status, error=error)
        except Exception as exc:
            log_error(ErrorCode.AC101, "SpanBuilder.close failed: %s", exc)
            return None

    def _close_impl(
        self,
        open_span: OpenSpan,
        *,
        outputs: Any | None,
        status: SpanStatus,
        error: ErrorInfo | None,
    ) -> Span:
        finalized = Span(
            span_id=open_span.span_id,
            parent_span_id=open_span.parent_span_id,
            trajectory_id=open_span.trajectory_id,
            name=open_span.name,
            type=open_span.type,
            start_time=open_span.start_time,
            end_time=_now_utc(),
            status=status,
            error=error,
            attributes=open_span.attributes,
            inputs=open_span.inputs,
            outputs=outputs,
            compliance=open_span.compliance,
            provenance=ProvenanceFields(
                content_hash="0" * 64,  # placeholder; replaced below
                parent_content_hash=None,
            ),
        )

        # Redaction runs BEFORE the hash is computed so content_hash covers
        # the bytes that actually ship. Architecture doc §4.4 + §8: hash
        # the post-redaction canonical form.
        if self._redaction_filter is not None:
            finalized = self._redaction_filter.apply(finalized)

        # content_hash is computed over canonical bytes with provenance excluded,
        # so the placeholder content_hash above doesn't affect the result.
        my_hash = content_hash(finalized)
        finalized = finalized.model_copy(
            update={
                "provenance": ProvenanceFields(
                    content_hash=my_hash,
                    parent_content_hash=None,
                )
            }
        )

        # Stamp every pending descendant under us with our hash, then ship them.
        for child in open_span._pending:
            stamped = child.model_copy(
                update={
                    "provenance": ProvenanceFields(
                        content_hash=child.provenance.content_hash,
                        parent_content_hash=my_hash,
                    )
                }
            )
            self._exporter.export(stamped)
        open_span._pending.clear()

        if open_span._parent is None:
            # Root: nothing to wait on. Ship ourselves directly.
            self._exporter.export(finalized)
            if self._flush_on_root_close:
                self._flush_exporter()
        else:
            # Non-root: park with parent. Parent will stamp parent_content_hash on close.
            open_span._parent._pending.append(finalized)

        return finalized

    def _flush_exporter(self) -> None:
        """Synchronously drain the exporter (serverless mode). Never raises."""
        flush = getattr(self._exporter, "flush", None)
        if flush is None:
            return
        try:
            flush()
        except Exception as exc:
            log_error(ErrorCode.AC416, "flush_on_root_close: exporter.flush raised: %s", exc)
