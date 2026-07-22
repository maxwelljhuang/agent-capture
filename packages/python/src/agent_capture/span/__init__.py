"""Span builder and lifecycle management.

The builder constructs :class:`~agent_capture.schema.span.Span` instances
from raw events, attaches compliance metadata, computes provenance hashes
at close time, and hands the finalized span to the exporter.

- :mod:`.builder` — :class:`SpanBuilder` factory: ``open(...)`` / ``close(...)``.
  Children buffer with their parent until parent closes; parent then stamps
  every pending descendant with ``parent_content_hash`` and ships the
  subtree to the exporter. The root span ships last.
"""

from agent_capture.span.builder import OpenSpan, SpanBuilder

__all__ = ["OpenSpan", "SpanBuilder"]
