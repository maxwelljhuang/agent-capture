"""FileExporter tests — JSONL round-trip + thread safety."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agent_capture.exporter.file import FileExporter
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import PlannerStepAttributes


def _make_span(i: int) -> Span:
    return Span(
        span_id=f"{i:016x}",
        parent_span_id=None,
        trajectory_id=f"{i:032x}",
        name=f"span-{i}",
        type=SpanType.PLANNER_STEP,
        start_time=datetime(2026, 5, 17, tzinfo=UTC),
        end_time=datetime(2026, 5, 17, tzinfo=UTC),
        attributes=PlannerStepAttributes(),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(content_hash="0" * 64),
    )


def test_round_trip_jsonl(tmp_path: Path) -> None:
    exporter = FileExporter(tmp_path / "out.jsonl")
    exporter.export(_make_span(1))
    exporter.export(_make_span(2))
    exporter.shutdown()

    lines = (tmp_path / "out.jsonl").read_text().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["name"] == "span-1"
    assert parsed[1]["name"] == "span-2"


def test_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "out.jsonl"
    FileExporter(path).export(_make_span(0))
    assert path.exists()


def test_thread_safe(tmp_path: Path) -> None:
    exporter = FileExporter(tmp_path / "out.jsonl")
    n_threads, per_thread = 8, 50

    def writer(start: int) -> None:
        for i in range(start, start + per_thread):
            exporter.export(_make_span(i))

    threads = [threading.Thread(target=writer, args=(t * per_thread,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "out.jsonl").read_text().splitlines()
    assert len(lines) == n_threads * per_thread
    # Every line is valid JSON — no torn writes.
    for line in lines:
        json.loads(line)


def test_export_never_raises_on_io_error(tmp_path: Path, monkeypatch) -> None:
    exporter = FileExporter(tmp_path / "out.jsonl")

    def boom(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr("pathlib.Path.open", boom)
    # Must NOT raise.
    exporter.export(_make_span(99))
