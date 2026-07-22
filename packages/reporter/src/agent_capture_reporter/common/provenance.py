"""Provenance helpers shared across renderers.

:func:`verify_hash_chain` is the single bool-returning chain check both the
report orchestrators use to set ``manifest.hash_chain_verified`` — it folds the
copy that previously lived privately in ``report.py``. (The richer,
error-raising variant used at load time stays in
:mod:`agent_capture_reporter.trajectory`, where the specific failure message
matters.)

:class:`GapLog` is the small accumulator both extractors use to collect
:class:`~agent_capture_reporter.common.manifest_base.ReportGap`s, so neither has
to re-implement the required/expected bookkeeping.
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_capture.schema import Span
from agent_capture.schema.canonical import content_hash

from agent_capture_reporter.common.manifest_base import GapSeverity, ReportGap


def verify_hash_chain(spans: Iterable[Span]) -> bool:
    """Recompute each span's content_hash and confirm it matches the recorded value.

    Also confirms each non-root span's ``parent_content_hash`` matches its
    parent's ``content_hash``. Returns ``True`` only if the whole chain holds.
    """
    span_list = list(spans)
    by_id = {s.span_id: s for s in span_list}
    for s in span_list:
        if content_hash(s) != s.provenance.content_hash:
            return False
        if s.parent_span_id is not None:
            parent = by_id.get(s.parent_span_id)
            if parent is None or s.provenance.parent_content_hash != parent.provenance.content_hash:
                return False
    return True


class GapLog:
    """Collects ReportGaps and tracks which scopes hit a required gap."""

    def __init__(self) -> None:
        self.gaps: list[ReportGap] = []
        self.required_scopes: list[str] = []

    def add(self, scope: str, severity: GapSeverity, reason: str) -> None:
        """Record a gap. Required gaps also accumulate into ``required_scopes``."""
        self.gaps.append(ReportGap(scope=scope, severity=severity, reason=reason))
        if severity == "required":
            self.required_scopes.append(scope)

    def required(self, scope: str, reason: str) -> None:
        """Shortcut for a required-severity gap."""
        self.add(scope, "required", reason)

    def expected(self, scope: str, reason: str) -> None:
        """Shortcut for an expected-severity gap."""
        self.add(scope, "expected", reason)
