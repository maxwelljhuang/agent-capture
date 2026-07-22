"""Corpus loading, period parsing, and model_call collection."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agent_capture.schema import Span

from agent_capture_reporter.common.corpus import ReportingPeriod, load_corpus
from agent_capture_reporter.errors import TrajectoryLoadError


def _dump(trajectories: list[list[Span]], directory) -> None:
    for i, spans in enumerate(trajectories):
        (directory / f"traj-{i}.jsonl").write_text(
            "\n".join(s.model_dump_json() for s in spans) + "\n", encoding="utf-8"
        )


def test_load_corpus_from_directory(inventory_corpus: list[list[Span]], tmp_path) -> None:
    _dump(inventory_corpus, tmp_path)
    corpus = load_corpus(tmp_path)
    assert len(corpus) == 4
    assert len(corpus.model_call_spans()) == 4


def test_load_corpus_multi_trajectory_file(inventory_corpus: list[list[Span]], tmp_path) -> None:
    # All trajectories' spans concatenated into one file → grouped by trajectory_id.
    path = tmp_path / "all.jsonl"
    all_spans = [s for spans in inventory_corpus for s in spans]
    path.write_text("\n".join(s.model_dump_json() for s in all_spans) + "\n", encoding="utf-8")
    corpus = load_corpus(path)
    assert len(corpus) == 4


def test_load_corpus_glob(inventory_corpus: list[list[Span]], tmp_path) -> None:
    _dump(inventory_corpus, tmp_path)
    corpus = load_corpus(str(tmp_path / "*.jsonl"))
    assert len(corpus) == 4


def test_load_corpus_empty_source(tmp_path) -> None:
    with pytest.raises(TrajectoryLoadError, match="no trajectory files"):
        load_corpus(tmp_path)


def test_reporting_period_parse_and_contains() -> None:
    period = ReportingPeriod.parse("2026-01-01:2026-03-31")
    assert period.contains(datetime(2026, 2, 15, tzinfo=UTC))
    assert period.contains(datetime(2026, 3, 31, 23, 0, 0, tzinfo=UTC))  # inclusive end-of-day
    assert not period.contains(datetime(2025, 12, 31, tzinfo=UTC))
    assert not period.contains(datetime(2026, 4, 1, tzinfo=UTC))


def test_reporting_period_rejects_inverted() -> None:
    with pytest.raises(ValueError, match="before start"):
        ReportingPeriod.parse("2026-03-31:2026-01-01")


def test_reporting_period_bad_format() -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ReportingPeriod.parse("last quarter")
