"""agent-capture — trajectory capture engine for AI agent compliance.

The public surface is intentionally small. Most users only need:

    from agent_capture import traced
    from agent_capture.exporter import FileExporter, configure

See ``docs/architecture.md`` at the repo root for the design rationale.
"""

from agent_capture.config import configure
from agent_capture.instrumentation import traced
from agent_capture.schema import (
    SCHEMA_VERSION,
    ComplianceMetadata,
    ErrorInfo,
    ProvenanceFields,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.span import SpanBuilder

__all__ = [
    "SCHEMA_VERSION",
    "ComplianceMetadata",
    "ErrorInfo",
    "ProvenanceFields",
    "Span",
    "SpanBuilder",
    "SpanStatus",
    "SpanType",
    "configure",
    "traced",
]

__version__ = "0.1.0"
