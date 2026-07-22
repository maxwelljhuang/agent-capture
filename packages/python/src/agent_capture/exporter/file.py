"""Synchronous JSON-lines file exporter.

For development, local testing, and air-gapped deployments. One JSON
object per line, UTF-8, append-mode. Thread-safe via a single lock.

This exporter blocks the caller on disk I/O. In production, wrap it in
:class:`agent_capture.exporter.queue.BoundedQueueExporter` so the agent's
hot path never waits on the filesystem.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span


class FileExporter(SpanExporter):
    """Append finalized spans to a local JSON-lines file."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def export(self, span: Span) -> None:
        if self._closed:
            return
        try:
            line = span.model_dump_json(exclude_none=False)
            with self._lock, self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
        except Exception as exc:
            log_error(ErrorCode.AC401, "FileExporter.export failed: %s", exc)

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._closed = True
