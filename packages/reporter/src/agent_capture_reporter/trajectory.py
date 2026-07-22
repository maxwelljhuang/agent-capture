"""The :class:`Trajectory` — an ordered, validated collection of spans.

A trajectory is the complete tree of spans an agent produced for a single
legally-consequential decision (see ``docs/architecture.md`` §2). The capture
engine ships spans individually; the ledger stores them; the reporter needs to
reason over the *whole* decision at once. This module is where the implicit
"group spans sharing a ``trajectory_id``" convention used by
``scripts/verify_trajectory.py`` becomes a first-class, immutable type.

The reporter is downstream of the ledger and assumes inputs are already
redacted and content-hashed. On load it nonetheless *recomputes* each span's
``content_hash`` and compares it against the recorded ``provenance.content_hash``
so a notice can never cite a span whose bytes no longer match what was recorded
— a tamper check, not a trust assumption.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agent_capture.schema import Span, SpanType
from agent_capture.schema.canonical import content_hash

from agent_capture_reporter.errors import TrajectoryLoadError


@dataclass(frozen=True)
class Trajectory:
    """An immutable, validated set of spans for one agent decision.

    Construct via :meth:`from_spans` or :func:`load_trajectory`; the bare
    constructor does no validation and is not part of the public contract.

    Attributes:
        trajectory_id: The shared 32-hex id every span carries.
        spans: All spans in the trajectory, in load order.
        root: The unique span with ``parent_span_id is None``.
    """

    trajectory_id: str
    spans: tuple[Span, ...]
    root: Span

    # --- construction -------------------------------------------------------

    @classmethod
    def from_spans(cls, spans: list[Span], *, verify_hashes: bool = True) -> Trajectory:
        """Validate a list of spans and build a Trajectory.

        Enforces the same structural invariants as
        ``scripts/verify_trajectory.py::_check_trajectory_structure`` so the
        reporter and the Week 6 verifier agree on what "well-formed" means:
        non-empty, exactly one root, a single shared ``trajectory_id``, no
        duplicate ``span_id``s, and every ``parent_span_id`` resolving.

        Args:
            spans: The spans to validate.
            verify_hashes: When ``True`` (default), recompute each span's
                ``content_hash`` and confirm it matches the recorded value, and
                confirm each non-root span's ``parent_content_hash`` matches its
                parent's ``content_hash``.

        Raises:
            TrajectoryLoadError: If any invariant fails.
        """
        if not spans:
            raise TrajectoryLoadError("trajectory is empty")

        trajectory_ids = {s.trajectory_id for s in spans}
        if len(trajectory_ids) != 1:
            raise TrajectoryLoadError(f"spans span multiple trajectories: {sorted(trajectory_ids)}")
        (trajectory_id,) = trajectory_ids

        by_id: dict[str, Span] = {}
        for s in spans:
            if s.span_id in by_id:
                raise TrajectoryLoadError(f"duplicate span_id in trajectory: {s.span_id}")
            by_id[s.span_id] = s

        roots = [s for s in spans if s.parent_span_id is None]
        if len(roots) != 1:
            raise TrajectoryLoadError(f"expected exactly one root span, got {len(roots)}")
        root = roots[0]

        for s in spans:
            if s.parent_span_id is not None and s.parent_span_id not in by_id:
                raise TrajectoryLoadError(
                    f"span {s.span_id} has parent_span_id={s.parent_span_id} not present in trajectory"
                )

        if verify_hashes:
            _verify_hashes(spans, by_id)

        return cls(trajectory_id=trajectory_id, spans=tuple(spans), root=root)

    # --- accessors used by the extractor ------------------------------------

    def by_type(self, span_type: SpanType) -> list[Span]:
        """Return all spans of ``span_type`` in load order."""
        return [s for s in self.spans if s.type is span_type]

    def by_id(self, span_id: str) -> Span | None:
        """Return the span with ``span_id``, or ``None`` if absent."""
        return next((s for s in self.spans if s.span_id == span_id), None)

    def children_of(self, span_id: str) -> list[Span]:
        """Return spans whose ``parent_span_id`` is ``span_id``."""
        return [s for s in self.spans if s.parent_span_id == span_id]

    def __iter__(self) -> Iterator[Span]:
        return iter(self.spans)

    def __len__(self) -> int:
        return len(self.spans)


def _verify_hashes(spans: list[Span], by_id: dict[str, Span]) -> None:
    """Recompute content hashes and verify the parent chain (tamper check)."""
    for s in spans:
        recomputed = content_hash(s)
        if recomputed != s.provenance.content_hash:
            raise TrajectoryLoadError(
                f"span {s.span_id} content_hash mismatch: recorded "
                f"{s.provenance.content_hash[:12]}… but canonical bytes hash to {recomputed[:12]}…"
            )
        if s.parent_span_id is None:
            if s.provenance.parent_content_hash is not None:
                raise TrajectoryLoadError(f"root span {s.span_id} has a non-null parent_content_hash")
            continue
        parent = by_id[s.parent_span_id]
        if s.provenance.parent_content_hash != parent.provenance.content_hash:
            raise TrajectoryLoadError(
                f"span {s.span_id} parent_content_hash does not match parent {parent.span_id}'s content_hash"
            )


def load_trajectory(path: Path | str, *, verify_hashes: bool = True) -> Trajectory:
    """Load a single trajectory from the exporter's JSONL file destination.

    Each non-blank line is one ``Span`` (the format the ``FileExporter`` writes;
    see ``agent_capture.exporter.file``). v1 expects exactly one trajectory per
    file — a file mixing trajectory_ids raises rather than silently picking one.

    Args:
        path: Path to a ``.jsonl`` trajectory file.
        verify_hashes: Forwarded to :meth:`Trajectory.from_spans`.

    Raises:
        TrajectoryLoadError: If the file is missing, unparseable, or does not
            contain exactly one well-formed trajectory.
    """
    path = Path(path)
    if not path.exists():
        raise TrajectoryLoadError(f"trajectory file does not exist: {path}")

    spans: list[Span] = []
    try:
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                spans.append(Span.model_validate(json.loads(raw)))
            except Exception as exc:
                raise TrajectoryLoadError(f"{path}:{lineno}: could not parse span: {exc}") from exc
    except OSError as exc:
        raise TrajectoryLoadError(f"could not read {path}: {exc}") from exc

    return Trajectory.from_spans(spans, verify_hashes=verify_hashes)
