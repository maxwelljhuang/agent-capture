"""Exporter — async shipping with backpressure handling.

A span flows: span builder → redaction filter → exporter queue → background
worker → destination. The agent's main execution never blocks.

Modules:

- :mod:`.base` — :class:`SpanExporter` protocol all destinations implement.
- :mod:`.queue` — bounded async queue with the critical-span-undroppable
  policy.
- :mod:`.file` — JSON-lines local file destination.
- :mod:`.http` (Week 4) — HTTPS POST to the vendor-cloud ledger endpoint.
- :mod:`.otel` (Week 4) — OTLP fan-out.
- :mod:`.retry`, :mod:`.shutdown` (Week 4) — failure handling.

Cardinal rule: the agent must always win. If the choice is between
dropping a span and crashing the host, drop the span — unless the span is
``human_approval`` or ``side_effect``, in which case wait briefly first.
"""

from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.file import FileExporter
from agent_capture.exporter.http import HTTPExporter
from agent_capture.exporter.queue import BoundedQueueExporter
from agent_capture.exporter.retry import RetryPolicy, with_retry
from agent_capture.exporter.routing import TenantRoutingExporter
from agent_capture.exporter.shutdown import (
    install_handlers,
    persist_to_spool,
    replay_spool,
)

__all__ = [
    "BoundedQueueExporter",
    "FileExporter",
    "HTTPExporter",
    "RetryPolicy",
    "SpanExporter",
    "TenantRoutingExporter",
    "install_handlers",
    "persist_to_spool",
    "replay_spool",
    "with_retry",
]
