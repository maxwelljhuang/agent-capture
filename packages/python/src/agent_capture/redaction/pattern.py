"""Pattern-based free-text redaction.

Runs the configured pattern recognizers (default: the finance pack in
:mod:`agent_capture.redaction.patterns_finance`) over any string the
filter is asked to redact. Replaces each match with the configured
strategy's output. Non-overlapping, left-to-right; longer matches win
where ranges collide.

Presidio integration is available behind the ``[redaction]`` extra; this
module's regex pack is the always-available baseline. Production
deployments that need ML-grade entity detection should layer Presidio's
analyzer on top by passing additional recognizers through
:class:`PatternRedactor`'s constructor.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from agent_capture.redaction.patterns_finance import (
    DEFAULT_RECOGNIZERS,
    Match,
    Recognizer,
)
from agent_capture.redaction.strategies import RedactionStrategy


class PatternRedactor:
    """Free-text redactor — applies a set of recognizers to a string.

    Args:
        recognizers: The recognizers to run. Defaults to the finance pack.
        strategy_for: A callable that maps ``field_type`` (e.g. ``"ssn"``)
            to the :class:`RedactionStrategy` the customer's policy
            assigned. The redactor itself owns no strategy — strategies
            come from the policy.
    """

    def __init__(
        self,
        *,
        strategy_for: Callable[[str], RedactionStrategy],
        recognizers: Iterable[Recognizer] = DEFAULT_RECOGNIZERS,
    ) -> None:
        self._recognizers = tuple(recognizers)
        self._strategy_for = strategy_for

    def redact(self, text: str) -> str:
        """Return ``text`` with every recognized match replaced.

        Non-overlapping replacement: when two recognizers match the same
        bytes, the longer match wins. Ties broken by recognizer order.
        """
        if not text:
            return text
        matches: list[Match] = []
        for r in self._recognizers:
            matches.extend(r.find_all(text))
        if not matches:
            return text
        matches = _resolve_overlaps(matches)
        # Apply right-to-left so earlier indexes stay valid.
        out = text
        for m in sorted(matches, key=lambda m: m.start, reverse=True):
            recognizer = next(r for r in self._recognizers if r.name == m.recognizer)
            replacement = self._strategy_for(recognizer.field_type).redact(m.value, field_type=recognizer.field_type)
            out = out[: m.start] + replacement + out[m.end :]
        return out


def _resolve_overlaps(matches: list[Match]) -> list[Match]:
    """Keep the longest non-overlapping subset of matches.

    Greedy: sort by length descending, then by start ascending. Walk and
    keep any match that doesn't intersect an already-kept range.
    """
    keepers: list[Match] = []
    for m in sorted(matches, key=lambda m: (-(m.end - m.start), m.start)):
        if any(not (m.end <= k.start or m.start >= k.end) for k in keepers):
            continue
        keepers.append(m)
    return keepers
