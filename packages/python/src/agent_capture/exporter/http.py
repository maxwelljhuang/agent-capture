"""HTTPS exporter — ships batches over HTTP to the vendor-cloud ledger.

Producer side (the agent's hot path) calls :meth:`export` which appends
the span to an internal batch buffer and returns immediately. A
background flush thread drains the buffer when either:

- the buffer reaches ``batch_size``, OR
- ``batch_max_wait_s`` has elapsed since the last flush.

On send failure: exponential backoff via :mod:`.retry` for transient
errors (5xx, network); permanent errors (4xx, payload too large) drop
after one attempt with a loud safelog entry. The agent never sees the
failure.

Cardinal rule (architecture doc §9.2): the agent must always win. If
the ledger is down for hours, we drop oldest spans rather than queue
unboundedly. The :class:`BoundedQueueExporter` upstream owns that drop
policy; this exporter just *attempts* delivery and reports outcomes via
the drop counter.

Payload shape: a JSON object with ``{"spans": [<span_json>, ...]}``.
Each span is the Pydantic ``model_dump_json`` form. ``Content-Type:
application/json``. Authorization is optional and configured at
construction.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any

import httpx

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.retry import RetryPolicy, with_retry
from agent_capture.schema import Span


class HTTPExporter(SpanExporter):
    """Batched HTTPS POST to the vendor-cloud ledger.

    Args:
        endpoint: URL of the ledger ingest endpoint (HTTPS in production).
        auth_token: Optional bearer token sent as ``Authorization: Bearer``.
        batch_size: Flush as soon as the buffer holds this many spans.
        batch_max_wait_s: Flush at most this many seconds after the first
            span enters an empty buffer.
        retry_policy: Backoff schedule for transient failures.
        timeout_s: Per-request HTTP timeout.
        client: Inject a pre-built ``httpx.Client`` for testing.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        auth_token: str | None = None,
        batch_size: int = 100,
        batch_max_wait_s: float = 1.0,
        retry_policy: RetryPolicy = RetryPolicy(),
        timeout_s: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._auth_token = auth_token
        self._batch_size = batch_size
        self._batch_max_wait_s = batch_max_wait_s
        self._retry = retry_policy
        self._client = client or httpx.Client(timeout=timeout_s)
        self._owns_client = client is None

        self._buffer: list[Span] = []
        self._lock = threading.Lock()
        self._has_work = threading.Event()
        self._shutdown = threading.Event()
        self._dropped = 0
        self._first_enqueue_time: float | None = None

        self._worker = threading.Thread(
            target=self._flush_loop,
            name="agent-capture-http",
            daemon=True,
        )
        self._worker.start()

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped

    # ---- SpanExporter -----------------------------------------------------

    def export(self, span: Span) -> None:
        if self._shutdown.is_set():
            return
        with self._lock:
            self._buffer.append(span)
            if self._first_enqueue_time is None:
                self._first_enqueue_time = time.monotonic()
            full = len(self._buffer) >= self._batch_size
        self._has_work.set()
        if full:
            # Optimistic flush hint; the worker handles the actual send so
            # the caller never blocks on I/O.
            self._has_work.set()

    def flush(self, timeout: float = 5.0) -> None:
        """Synchronously send the current buffer. Does not block the worker.

        Lets a caller ship within a request window on platforms that throttle
        background-thread CPU between requests (e.g. Cloud Run). Never raises.
        """
        with self._lock:
            batch = self._buffer
            self._buffer = []
            self._first_enqueue_time = None
        if batch:
            self._send_with_retry(batch)

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        self._has_work.set()
        self._worker.join(timeout=timeout)
        # Final drain in case the worker missed something.
        with self._lock:
            remaining = self._buffer
            self._buffer = []
        if remaining:
            self._send_with_retry(remaining)
        if self._owns_client:
            with contextlib.suppress(Exception):
                self._client.close()

    # ---- internals --------------------------------------------------------

    def _flush_loop(self) -> None:
        while True:
            self._has_work.wait(timeout=self._batch_max_wait_s)
            self._has_work.clear()
            if self._shutdown.is_set() and not self._buffer:
                return
            self._maybe_flush()
            if self._shutdown.is_set() and not self._buffer:
                return

    def _maybe_flush(self) -> None:
        with self._lock:
            if not self._buffer:
                self._first_enqueue_time = None
                return
            age = time.monotonic() - self._first_enqueue_time if self._first_enqueue_time is not None else 0.0
            should_flush = (
                len(self._buffer) >= self._batch_size or age >= self._batch_max_wait_s or self._shutdown.is_set()
            )
            if not should_flush:
                return
            batch = self._buffer
            self._buffer = []
            self._first_enqueue_time = None
        self._send_with_retry(batch)

    def _send_with_retry(self, batch: list[Span]) -> None:
        if not batch:
            return
        try:
            payload = self._serialize(batch)
        except Exception as exc:
            log_error(
                ErrorCode.AC402,
                "HTTPExporter: serialization failed; dropping %d spans: %s",
                len(batch),
                exc,
            )
            with self._lock:
                self._dropped += len(batch)
            return

        def attempt() -> None:
            resp = self._client.post(
                self._endpoint,
                content=payload,
                headers=self._headers(),
            )
            # Permanent failures (4xx) are non-retryable. Raise an
            # exception that the retry predicate identifies as permanent.
            if 400 <= resp.status_code < 500:
                raise _PermanentHTTPError(resp.status_code, resp.text[:512])
            resp.raise_for_status()

        try:
            with_retry(
                attempt,
                policy=self._retry,
                retryable=lambda exc: not isinstance(exc, _PermanentHTTPError),
            )
        except _PermanentHTTPError as exc:
            log_error(
                ErrorCode.AC403,
                "HTTPExporter: ledger rejected batch of %d spans with HTTP %d: %s",
                len(batch),
                exc.status,
                exc.body,
                exc_info=False,
            )
            with self._lock:
                self._dropped += len(batch)
        except Exception as exc:
            log_error(
                ErrorCode.AC404,
                "HTTPExporter: dropping %d spans after retries: %s",
                len(batch),
                exc,
            )
            with self._lock:
                self._dropped += len(batch)

    def _serialize(self, batch: list[Span]) -> bytes:
        # Use Pydantic's per-span JSON dump and stitch by hand — avoids
        # an extra dict round-trip and keeps the canonical bytes intact.
        body: dict[str, Any] = {
            "spans": [json.loads(s.model_dump_json(exclude_none=False)) for s in batch],
        }
        return json.dumps(body, separators=(",", ":")).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self._auth_token:
            h["authorization"] = f"Bearer {self._auth_token}"
        return h


class _PermanentHTTPError(Exception):
    """Raised on 4xx so the retry helper skips further attempts."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body
