"""Prometheus metrics for the enforcement engine."""

from __future__ import annotations

from prometheus_client import Counter

advisory_verdicts = Counter(
    "enforcement_advisory_verdicts_total",
    "Advisory enforcement verdicts produced at the ledger ingest boundary.",
    ["result", "span_type"],
)
