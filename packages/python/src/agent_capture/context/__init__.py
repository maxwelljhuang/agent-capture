"""Context propagation.

The "current parent span" pointer cannot be a global. When an agent fans
out parallel work (asyncio tasks, threads, sub-process calls), each branch
must inherit its own copy of the parent pointer so spans created inside
attribute correctly even though branches interleave.

- :mod:`.propagation` — :func:`current_parent`, :func:`span_scope`,
  :func:`run_in_context` wrapping ``contextvars.ContextVar``.
- :mod:`.w3c` (Week 3) — W3C Trace Context inject/extract helpers for HTTP
  and message-queue boundaries, built on
  ``opentelemetry.propagators.textmap``.
"""

from agent_capture.context.propagation import (
    bind_context,
    current_parent,
    model_call_suppressed,
    span_scope,
    suppress_model_call_capture,
)
from agent_capture.context.w3c import RemoteParent, extract, inject

__all__ = [
    "RemoteParent",
    "bind_context",
    "current_parent",
    "extract",
    "inject",
    "model_call_suppressed",
    "span_scope",
    "suppress_model_call_capture",
]
