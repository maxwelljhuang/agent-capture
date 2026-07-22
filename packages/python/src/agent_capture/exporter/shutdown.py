"""Graceful shutdown — drain queue, persist unshipped spans, replay on startup.

The exporter pipeline (typically :class:`BoundedQueueExporter` →
:class:`HTTPExporter`) holds in-flight spans in memory. On a clean exit
(``atexit``) or signal (``SIGTERM``/``SIGINT``), :func:`install_handlers`
ensures the pipeline's ``shutdown`` runs and any still-buffered spans
are persisted to ``~/.agent-capture/spool/<timestamp>-<pid>.jsonl`` so
the next agent process can replay them via :func:`replay_spool`.

This module is *not* about durable delivery — full durability is the
ledger's job. It's about not losing data on a clean exit, and about
ferrying spans across an agent restart when the customer's deployment
makes that possible.
"""

from __future__ import annotations

import atexit
import os
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import Span

DEFAULT_SPOOL_DIR = Path.home() / ".agent-capture" / "spool"


# ---- shutdown handlers ---------------------------------------------------


def install_handlers(
    exporter: SpanExporter,
    *,
    drain_unshipped: Callable[[], list[Span]] | None = None,
    spool_dir: Path | None = None,
    timeout_s: float = 5.0,
) -> None:
    """Wire ``atexit`` and SIGTERM/SIGINT handlers to ``exporter.shutdown``.

    Args:
        exporter: The pipeline to shut down. Typically a
            :class:`BoundedQueueExporter`.
        drain_unshipped: Optional callable returning any spans that the
            shutdown didn't manage to deliver. Those get persisted to the
            spool dir for replay. If omitted, no persistence happens —
            ``shutdown`` is best-effort flush only.
        spool_dir: Where to write the spool file. Defaults to
            ``~/.agent-capture/spool``.
        timeout_s: How long to wait for the pipeline to drain.
    """
    dir_ = spool_dir or DEFAULT_SPOOL_DIR
    state = {"done": False}
    lock = threading.Lock()

    def run_shutdown() -> None:
        with lock:
            if state["done"]:
                return
            state["done"] = True
        try:
            exporter.shutdown(timeout=timeout_s)
        except Exception as exc:
            log_error(ErrorCode.AC412, "shutdown: exporter.shutdown raised: %s", exc)
        if drain_unshipped is None:
            return
        try:
            leftover = drain_unshipped()
        except Exception as exc:
            log_error(ErrorCode.AC413, "shutdown: drain_unshipped raised: %s", exc)
            return
        if leftover:
            persist_to_spool(leftover, spool_dir=dir_)

    atexit.register(run_shutdown)

    # Install signal handlers without clobbering existing ones — chain.
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)

        def handler(signum: int, frame, _previous=previous) -> None:  # type: ignore[no-untyped-def]
            run_shutdown()
            if callable(_previous):
                _previous(signum, frame)
            elif _previous == signal.SIG_DFL:
                # Restore default and re-raise so the OS sees a normal exit code.
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # Non-main thread — can't install signal handlers. atexit suffices.
            pass


# ---- spool persistence ---------------------------------------------------


def persist_to_spool(spans: list[Span], *, spool_dir: Path | None = None) -> Path:
    """Write spans as JSON lines under ``spool_dir``. Returns the path written."""
    dir_ = spool_dir or DEFAULT_SPOOL_DIR
    dir_.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time()):d}-{os.getpid()}.jsonl"
    path = dir_ / name
    try:
        with path.open("a", encoding="utf-8") as fh:
            for s in spans:
                fh.write(s.model_dump_json(exclude_none=False))
                fh.write("\n")
    except Exception as exc:
        log_error(ErrorCode.AC408, "persist_to_spool failed: %s", exc)
    return path


def replay_spool(
    exporter: SpanExporter,
    *,
    spool_dir: Path | None = None,
    delete_after: bool = True,
) -> int:
    """Read every ``*.jsonl`` under ``spool_dir`` and re-export its spans.

    Returns the count of spans replayed. Files are deleted only on a
    fully-successful read+export to avoid double-shipping if export
    fails mid-stream.
    """
    dir_ = spool_dir or DEFAULT_SPOOL_DIR
    if not dir_.exists():
        return 0
    count = 0
    for path in sorted(dir_.glob("*.jsonl")):
        try:
            spans: list[Span] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                spans.append(Span.model_validate_json(line))
        except Exception as exc:
            log_error(ErrorCode.AC409, "replay_spool: skipping %s due to: %s", path, exc)
            continue
        try:
            for s in spans:
                exporter.export(s)
            count += len(spans)
        except Exception as exc:
            log_error(ErrorCode.AC410, "replay_spool: export failed for %s: %s", path, exc)
            continue
        if delete_after:
            try:
                path.unlink()
            except OSError as exc:
                log_error(
                    ErrorCode.AC411,
                    "replay_spool: could not delete %s: %s",
                    path,
                    exc,
                    exc_info=False,
                )
    return count
