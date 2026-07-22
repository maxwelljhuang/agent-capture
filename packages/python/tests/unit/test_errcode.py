"""Error code logging helper tests."""

from __future__ import annotations

import logging

import pytest

from agent_capture._internal.safelog import REMEDIATION, ErrorCode, log_error


@pytest.fixture
def captured() -> list[logging.LogRecord]:
    """Capture every record emitted on the SDK logger."""
    logger = logging.getLogger("agent_capture")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    h = _Capture(level=logging.DEBUG)
    logger.addHandler(h)
    prev_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(h)
        logger.setLevel(prev_level)


def test_log_error_prepends_stable_code(captured: list[logging.LogRecord]) -> None:
    log_error(ErrorCode.AC401, "FileExporter.export failed: %s", "disk full", exc_info=False)
    assert len(captured) == 1
    msg = captured[0].getMessage()
    assert msg.startswith("[AC401] ")
    assert "FileExporter.export failed: disk full" in msg


def test_log_error_appends_remediation_hint(captured: list[logging.LogRecord]) -> None:
    log_error(ErrorCode.AC404, "HTTPExporter exhausted retries: %s", "timeout", exc_info=False)
    assert " — fix: " in captured[0].getMessage()
    assert "ledger is unreachable" in captured[0].getMessage()


def test_every_error_code_has_a_remediation() -> None:
    """Every ErrorCode value must be present in the REMEDIATION map."""
    missing = [c for c in ErrorCode if c not in REMEDIATION]
    assert not missing, f"ErrorCode values missing remediations: {missing}"


def test_log_error_no_args_passes_message_verbatim(captured: list[logging.LogRecord]) -> None:
    log_error(ErrorCode.AC301, "plain message", exc_info=False)
    assert "plain message" in captured[0].getMessage()
