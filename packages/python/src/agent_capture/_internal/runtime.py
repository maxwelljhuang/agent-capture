"""Process-wide singletons for the running capture engine.

The :func:`agent_capture.configure` entry point registers a default
:class:`SpanBuilder` here. The :func:`agent_capture.instrumentation.decorator.traced`
helper reads from it. Tests can either call :func:`set_default_builder`
explicitly or pass an explicit builder to ``traced(..., builder=...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_capture.span.builder import SpanBuilder

_default_builder: SpanBuilder | None = None


def set_default_builder(builder: SpanBuilder | None) -> None:
    """Register the process-wide default builder. Pass ``None`` to clear."""
    global _default_builder
    _default_builder = builder


def default_builder() -> SpanBuilder | None:
    """Return the process-wide default builder, or ``None`` if unconfigured."""
    return _default_builder
