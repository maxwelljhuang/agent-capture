"""Trajectory construction, loading, and tamper detection."""

from __future__ import annotations

import json

import pytest
from agent_capture.schema import Span, SpanType

from agent_capture_reporter.errors import TrajectoryLoadError
from agent_capture_reporter.trajectory import Trajectory, load_trajectory


def _write_jsonl(spans: list[Span], path) -> None:
    path.write_text("\n".join(s.model_dump_json() for s in spans) + "\n", encoding="utf-8")


def test_from_spans_builds_valid_trajectory(loan_denial_spans: list[Span]) -> None:
    traj = Trajectory.from_spans(loan_denial_spans)
    assert len(traj) == 7
    assert traj.root.parent_span_id is None
    assert traj.trajectory_id == loan_denial_spans[0].trajectory_id
    assert len(traj.by_type(SpanType.MODEL_CALL)) == 1
    assert len(traj.children_of(traj.root.span_id)) == 6


def test_empty_trajectory_rejected() -> None:
    with pytest.raises(TrajectoryLoadError, match="empty"):
        Trajectory.from_spans([])


def test_multiple_roots_rejected(loan_denial_spans: list[Span]) -> None:
    extra_root = loan_denial_spans[0].model_copy(update={"span_id": "9999999999999999"})
    with pytest.raises(TrajectoryLoadError, match="exactly one root"):
        Trajectory.from_spans([*loan_denial_spans, extra_root], verify_hashes=False)


def test_duplicate_span_id_rejected(loan_denial_spans: list[Span]) -> None:
    dup = loan_denial_spans[1]
    with pytest.raises(TrajectoryLoadError, match="duplicate span_id"):
        Trajectory.from_spans([*loan_denial_spans, dup], verify_hashes=False)


def test_dangling_parent_rejected(loan_denial_spans: list[Span]) -> None:
    orphan = loan_denial_spans[1].model_copy(update={"parent_span_id": "deadbeefdeadbeef"})
    spans = [loan_denial_spans[0], orphan, *loan_denial_spans[2:]]
    with pytest.raises(TrajectoryLoadError, match="not present"):
        Trajectory.from_spans(spans, verify_hashes=False)


def test_load_trajectory_round_trip(loan_denial_spans: list[Span], tmp_path) -> None:
    path = tmp_path / "decision.jsonl"
    _write_jsonl(loan_denial_spans, path)
    traj = load_trajectory(path)
    assert len(traj) == 7
    assert traj.trajectory_id == loan_denial_spans[0].trajectory_id


def test_load_trajectory_missing_file(tmp_path) -> None:
    with pytest.raises(TrajectoryLoadError, match="does not exist"):
        load_trajectory(tmp_path / "nope.jsonl")


def test_tampered_span_detected(loan_denial_spans: list[Span], tmp_path) -> None:
    # Mutate a span's content without updating its recorded content_hash.
    tampered = loan_denial_spans[3].model_copy(update={"name": "score_application_TAMPERED"})
    spans = [*loan_denial_spans[:3], tampered, *loan_denial_spans[4:]]
    path = tmp_path / "decision.jsonl"
    _write_jsonl(spans, path)
    with pytest.raises(TrajectoryLoadError, match="content_hash mismatch"):
        load_trajectory(path)


def test_multiple_trajectories_in_file_rejected(loan_denial_spans: list[Span], tmp_path) -> None:
    other = loan_denial_spans[0].model_copy(update={"trajectory_id": "f" * 32, "span_id": "8888888888888888"})
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        json.dumps(json.loads(loan_denial_spans[0].model_dump_json())) + "\n" + other.model_dump_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TrajectoryLoadError, match="multiple trajectories"):
        load_trajectory(path, verify_hashes=False)
