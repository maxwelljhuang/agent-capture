"""The :class:`SpanExporter` protocol.

Every destination (file, HTTP, OTLP) implements this protocol. The span
builder, the decorator, and the bounded queue all hold a reference typed
as :class:`SpanExporter`, so swapping destinations is a one-line change at
``configure(...)`` time.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_capture.schema import Span


@runtime_checkable
class SpanExporter(Protocol):
    """Destination for finalized spans.

    Implementations must be thread-safe. They are called from the
    background drain worker in :class:`agent_capture.exporter.queue.BoundedQueueExporter`
    or, in tests, directly from the producing thread.
    """

    def export(self, span: Span) -> None:
        """Persist or ship a single finalized span.

        Must not raise into the caller. Internal failures are logged via
        :mod:`agent_capture._internal.safelog`. The cardinal rule (from the
        architecture doc §9.2): the agent must always win.
        """
        ...

    def shutdown(self, timeout: float = 5.0) -> None:
        """Flush any buffered state and release resources.

        Idempotent. After ``shutdown`` returns, further calls to ``export``
        may be silently dropped — callers should treat ``shutdown`` as
        terminal.
        """
        ...
