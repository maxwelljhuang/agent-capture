"""Bounded async exporter — buffers spans and drains via a background worker.

Wraps any :class:`~agent_capture.exporter.base.SpanExporter`. Producers
call :meth:`BoundedQueueExporter.export` from the agent's hot path. The
producer never blocks on the underlying destination's I/O.

Drop policy (architecture doc §9.1, CLAUDE.md non-negotiable rule #2):

- When the queue is full and the span is *not* critical, drop the oldest
  span in the queue and enqueue the new one. Increment a drop counter for
  vendor observability.
- When the queue is full and the span *is* critical
  (``human_approval`` or ``side_effect``), wait briefly for the worker to
  catch up. Critical spans are never silently dropped — losing them defeats
  the compliance purpose. If the wait exceeds ``critical_block_timeout``
  the span is logged and dropped with a *loud* safelog entry.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Final

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span, SpanType

_CRITICAL_TYPES: Final[frozenset[SpanType]] = frozenset(
    {SpanType.HUMAN_APPROVAL, SpanType.SIDE_EFFECT},
)


class BoundedQueueExporter(SpanExporter):
    """Non-blocking exporter front-end with a background drain worker."""

    def __init__(
        self,
        inner: SpanExporter,
        *,
        max_size: int = 10_000,
        critical_block_timeout: float = 1.0,
        thread_name: str = "agent-capture-exporter",
    ) -> None:
        self._inner = inner
        self._queue: queue.Queue[Span | None] = queue.Queue(maxsize=max_size)
        self._critical_block_timeout = critical_block_timeout
        self._dropped_count = 0
        self._dropped_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._worker = threading.Thread(
            target=self._drain_loop,
            name=thread_name,
            daemon=True,
        )
        self._worker.start()

    @property
    def dropped_count(self) -> int:
        with self._dropped_lock:
            return self._dropped_count

    def export(self, span: Span) -> None:
        if self._shutdown.is_set():
            self._record_drop()
            return
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            self._handle_full(span)

    def _handle_full(self, span: Span) -> None:
        if span.type in _CRITICAL_TYPES:
            deadline = time.monotonic() + self._critical_block_timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log_error(
                        ErrorCode.AC406,
                        "Dropped CRITICAL span (%s) after %.2fs block — "
                        "queue saturated, inner exporter cannot keep up.",
                        span.type.value,
                        self._critical_block_timeout,
                        exc_info=False,
                    )
                    self._record_drop()
                    return
                try:
                    self._queue.put(span, timeout=remaining)
                    return
                except queue.Full:
                    continue
        # Non-critical: drop oldest, enqueue new.
        with contextlib.suppress(queue.Empty):
            self._queue.get_nowait()
        self._record_drop()
        try:
            self._queue.put_nowait(span)
        except queue.Full:
            # Worker has caught up; we already counted the drop above.
            pass

    def _record_drop(self) -> None:
        with self._dropped_lock:
            self._dropped_count += 1

    def _drain_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # shutdown sentinel
                self._queue.task_done()
                return
            try:
                self._inner.export(item)
            except Exception as exc:
                log_error(ErrorCode.AC405, "inner exporter failed: %s", exc)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """Drain queued spans to the inner exporter, then flush it synchronously.

        Best-effort and bounded by ``timeout``. Intended to be called within a
        request window so spans ship even on platforms that throttle the
        background drain thread's CPU between requests (e.g. Cloud Run). Lets
        the host keep ``cpu_idle=true`` without losing spans. Never raises.
        """
        deadline = time.monotonic() + timeout
        # Wait for the drain worker to hand every queued span to the inner
        # exporter (unfinished_tasks hits 0 only after each task_done).
        while getattr(self._queue, "unfinished_tasks", 0) > 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        inner_flush = getattr(self._inner, "flush", None)
        if callable(inner_flush):
            try:
                inner_flush(timeout=max(0.0, deadline - time.monotonic()))
            except Exception as exc:  # never raise into the host
                log_error(ErrorCode.AC405, "inner exporter flush failed: %s", exc)

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        # Drain remaining items, then signal worker to exit.
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            log_error(
                ErrorCode.AC407,
                "BoundedQueueExporter.shutdown: queue full at exit",
                exc_info=False,
            )
        self._worker.join(timeout=timeout)
        self._inner.shutdown(timeout=timeout)
