"""Prometheus metrics for the ledger.

Single shared registry. Each metric lives at module scope so import in any
file works without re-registration.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry()

ingest_spans = Counter(
    "ledger_ingest_spans_total",
    "Spans seen by ingest, by outcome.",
    labelnames=("result", "type"),
    registry=REGISTRY,
)

ingest_latency = Histogram(
    "ledger_ingest_latency_seconds",
    "End-to-end ingest latency per batch.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
    registry=REGISTRY,
)

ingest_batch = Histogram(
    "ledger_ingest_batch_size",
    "Span count per ingest batch.",
    buckets=(1, 10, 25, 50, 100, 250, 500, 1000),
    registry=REGISTRY,
)

chain_failures = Counter(
    "ledger_chain_verification_failures_total",
    "Hash/chain/schema verification failures by error code.",
    labelnames=("code",),
    registry=REGISTRY,
)

inflight_spans = Gauge(
    "ledger_inflight_spans",
    "Spans currently being ingested (backpressure).",
    registry=REGISTRY,
)

quarantine_size = Gauge(
    "ledger_quarantine_size",
    "Rows currently in quarantine.",
    registry=REGISTRY,
)

attestations_created = Counter(
    "ledger_attestations_created_total",
    "Signed Merkle attestations emitted.",
    registry=REGISTRY,
)

attestation_export_failures = Counter(
    "ledger_attestation_export_failures_total",
    "Attestation sink export failures, by sink.",
    labelnames=("sink",),
    registry=REGISTRY,
)

retention_deleted = Counter(
    "ledger_retention_spans_deleted_total",
    "Spans deleted by the retention worker.",
    labelnames=("class", "kind"),
    registry=REGISTRY,
)
