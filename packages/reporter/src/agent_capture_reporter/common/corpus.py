"""Load a *corpus* of trajectories for aggregate reports (SR 11-7 and beyond).

ECOA renders one trajectory; SR 11-7 reasons over many at once. :class:`Corpus`
is the immutable collection, built on the same per-line ``Span.model_validate``
path and the same :meth:`Trajectory.from_spans` validation the single-trajectory
loader uses — so corpus members are held to the identical well-formedness and
hash-chain invariants.

A corpus source can be a directory (all ``*.jsonl``), a glob, an explicit list
of paths, or a single file. Spans are grouped by ``trajectory_id``, so a file
holding many trajectories' spans is split correctly.
"""

from __future__ import annotations

import glob as globlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_capture.schema import Span, SpanType

from agent_capture_reporter.errors import TrajectoryLoadError
from agent_capture_reporter.trajectory import Trajectory


@dataclass(frozen=True)
class ReportingPeriod:
    """An inclusive UTC time window for an aggregate report."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"reporting period end {self.end} is before start {self.start}")

    def contains(self, when: datetime) -> bool:
        """Return whether ``when`` falls in ``[start, end]`` (compared in UTC)."""
        moment = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
        return self.start <= moment <= self.end

    @classmethod
    def parse(cls, text: str) -> ReportingPeriod:
        """Parse ``YYYY-MM-DD:YYYY-MM-DD`` into an inclusive day-granularity window."""
        parts = text.split(":")
        if len(parts) != 2:
            raise ValueError(f"reporting period must be 'YYYY-MM-DD:YYYY-MM-DD', got {text!r}")
        start_date = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=UTC)
        end_date = datetime.strptime(parts[1], "%Y-%m-%d").replace(tzinfo=UTC)
        end_of_day = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        return cls(start=start_date, end=end_of_day)


@dataclass(frozen=True)
class Corpus:
    """An immutable collection of trajectories for an aggregate report."""

    trajectories: tuple[Trajectory, ...]

    def all_spans(self) -> Iterator[Span]:
        """Yield every span across every trajectory."""
        for traj in self.trajectories:
            yield from traj.spans

    def model_call_spans(self) -> list[Span]:
        """Return every ``model_call`` span across the corpus."""
        return [s for s in self.all_spans() if s.type is SpanType.MODEL_CALL]

    def __iter__(self) -> Iterator[Trajectory]:
        return iter(self.trajectories)

    def __len__(self) -> int:
        return len(self.trajectories)


def _resolve_files(source: Path | str | list[Path | str]) -> list[Path]:
    """Resolve a corpus source into a sorted list of files."""
    if isinstance(source, list):
        files = [Path(p) for p in source]
    else:
        path = Path(source)
        if path.is_dir():
            files = sorted(path.glob("*.jsonl"))
        elif any(ch in str(source) for ch in "*?["):
            files = sorted(Path(p) for p in globlib.glob(str(source)))
        else:
            files = [path]
    if not files:
        raise TrajectoryLoadError(f"no trajectory files found for source: {source}")
    for f in files:
        if not f.exists():
            raise TrajectoryLoadError(f"trajectory file does not exist: {f}")
    return files


def load_corpus(
    source: Path | str | list[Path | str],
    *,
    verify_hashes: bool = True,
) -> Corpus:
    """Load a corpus of trajectories from files, grouping spans by trajectory_id.

    Args:
        source: A directory (all ``*.jsonl``), a glob string, an explicit list of
            paths, or a single file.
        verify_hashes: Forwarded to :meth:`Trajectory.from_spans` for each group.

    Raises:
        TrajectoryLoadError: If no files are found, a file is unparseable, or a
            trajectory group fails validation.
    """
    files = _resolve_files(source)
    by_trajectory: dict[str, list[Span]] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise TrajectoryLoadError(f"could not read {path}: {exc}") from exc
        for lineno, raw in enumerate(lines, start=1):
            if not raw.strip():
                continue
            try:
                span = Span.model_validate(json.loads(raw))
            except Exception as exc:  # wrap parse/validation into our error type
                raise TrajectoryLoadError(f"{path}:{lineno}: could not parse span: {exc}") from exc
            by_trajectory.setdefault(span.trajectory_id, []).append(span)

    trajectories = tuple(
        Trajectory.from_spans(spans, verify_hashes=verify_hashes) for _, spans in sorted(by_trajectory.items())
    )
    return Corpus(trajectories=trajectories)
