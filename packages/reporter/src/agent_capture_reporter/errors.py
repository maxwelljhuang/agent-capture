"""Reporter errors (layer 3).

The reporter runs offline, downstream of the ledger, not inside the host
agent's hot path — so unlike the capture engine it is *allowed* to raise. A
compliance notice that cannot be backed by the recorded trajectory must fail
loudly rather than emit a legally-deficient document.

The hierarchy mirrors the failure boundaries:

* :class:`TrajectoryLoadError` — the input file could not be parsed or did not
  contain exactly one well-formed trajectory.
* :class:`IncompleteTrajectoryError` — the trajectory is well-formed but is
  missing content that Regulation B *requires* in the notice (e.g. the decision
  or the principal reasons). Carries the list of missing required elements.
* :class:`RenderError` — the notice template or PDF backend failed.
"""

from __future__ import annotations


class ReporterError(Exception):
    """Base class for all reporter errors."""


class TrajectoryLoadError(ReporterError):
    """The trajectory could not be loaded or is structurally invalid.

    Raised for unparseable JSONL, multiple trajectories in one file, missing or
    duplicate roots, dangling parent links, or a content-hash mismatch that
    means a span no longer hashes to the value recorded for it.
    """


class IncompleteTrajectoryError(ReporterError):
    """The trajectory lacks a legally-required element, so no notice is produced.

    Attributes:
        missing: Stable section ids of the required elements that were absent
            (e.g. ``["decision", "principal_reasons"]``).
    """

    def __init__(self, missing: list[str], detail: str) -> None:
        self.missing = missing
        super().__init__(detail)


class RenderError(ReporterError):
    """The HTML template or the PDF backend failed to produce output."""


class IncompleteInventoryError(ReporterError):
    """The corpus cannot produce an SR 11-7 model inventory at all.

    Unlike per-model governance gaps (which are *rendered as findings*), this is
    raised only for conditions where no inventory can be formed: an empty corpus
    or one with no model usage in the reporting period, a missing/invalid
    period, or an ambiguous multi-tenant corpus with no tenant filter.

    Attributes:
        reason_code: A stable code for the failure (e.g. ``"empty_corpus"``).
    """

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(detail)
