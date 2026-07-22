"""Shutdown handlers + spool round-trip tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.shutdown import (
    persist_to_spool,
    replay_spool,
)
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import (
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import PlannerStepAttributes


def _span(i: int) -> Span:
    return Span(
        span_id=f"{i:016x}",
        parent_span_id=None,
        trajectory_id=f"{i:032x}",
        name=f"s{i}",
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


class _Cap(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def test_persist_and_replay_round_trip(tmp_path: Path) -> None:
    persist_to_spool([_span(1), _span(2), _span(3)], spool_dir=tmp_path)
    exporter = _Cap()
    count = replay_spool(exporter, spool_dir=tmp_path)
    assert count == 3
    assert [s.span_id for s in exporter.spans] == [f"{i:016x}" for i in (1, 2, 3)]
    # File deleted after successful replay.
    assert list(tmp_path.glob("*.jsonl")) == []


def test_replay_skips_corrupt_files(tmp_path: Path) -> None:
    persist_to_spool([_span(1)], spool_dir=tmp_path)
    corrupt = tmp_path / "broken.jsonl"
    corrupt.write_text("{not json", encoding="utf-8")
    exporter = _Cap()
    count = replay_spool(exporter, spool_dir=tmp_path)
    assert count == 1  # corrupt file skipped, good one replayed
    # Corrupt file left in place for ops to inspect.
    assert corrupt.exists()


def test_replay_empty_dir_returns_zero(tmp_path: Path) -> None:
    exporter = _Cap()
    assert replay_spool(exporter, spool_dir=tmp_path) == 0


def test_replay_nonexistent_dir_returns_zero(tmp_path: Path) -> None:
    exporter = _Cap()
    assert replay_spool(exporter, spool_dir=tmp_path / "nope") == 0


def test_delete_after_false_keeps_file(tmp_path: Path) -> None:
    persist_to_spool([_span(1)], spool_dir=tmp_path)
    exporter = _Cap()
    replay_spool(exporter, spool_dir=tmp_path, delete_after=False)
    assert list(tmp_path.glob("*.jsonl"))  # not deleted
