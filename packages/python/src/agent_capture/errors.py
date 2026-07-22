"""Internal SDK errors.

These types never reach the host agent's code path. The public entry points
in :mod:`agent_capture` catch every exception, log it via the safelog, and
return the agent's original return value untouched. This module exists so
internal callers can ``raise`` typed errors at module boundaries while still
preserving the cardinal rule: *the agent must always win*.
"""

from __future__ import annotations


class CaptureError(Exception):
    """Base class for all internal capture-engine errors."""


class SchemaViolation(CaptureError):
    """A constructed span did not conform to the canonical schema."""


class ExporterError(CaptureError):
    """A destination rejected, timed out, or otherwise failed to accept spans."""


class RedactionError(CaptureError):
    """The redaction filter failed. Filter callers must downgrade to full redaction."""


class ContextPropagationError(CaptureError):
    """Parent-child wiring is inconsistent — typically a misused context manager."""
