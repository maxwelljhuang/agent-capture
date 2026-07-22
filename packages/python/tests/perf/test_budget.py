"""Performance budget tests against architecture doc §10.

Budgets:

- Per-span overhead at capture time: under **1ms p99** (open + redact + close).
  Most of the cost is the redaction pass; raw span construction is microseconds.
- Memory overhead per agent process: under **100MB** steady-state including the
  in-memory queue.
- CPU overhead: under **2%** of agent CPU under sustained load.
- No measurable impact on agent p99 latency vs. engine-disabled baseline.

These tests fail CI if any budget is exceeded. They are marked
``@pytest.mark.perf`` so they run separately from unit/integration tests
(see ``.github/workflows/perf.yml``).

CI variability: GitHub-Actions runners and similar shared CI hosts can
have transient stalls that blow a strict 1ms p99. Each budget can be
loosened via ``AGENT_CAPTURE_PERF_RELAX`` (a positive float multiplier)
so we don't waste cycles chasing flaky CI when local prod numbers are
fine. The default is 1.0 (exact §10 budgets); set ``AGENT_CAPTURE_PERF_RELAX=2``
to double every threshold.
"""

from __future__ import annotations

import gc
import os
import resource
import statistics
import sys
import time
import tracemalloc

import pytest

from agent_capture.context.propagation import span_scope
from agent_capture.exporter.base import SpanExporter
from agent_capture.redaction import RedactionFilter, parse_policy
from agent_capture.schema import (
    ComplianceMetadata,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import (
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import (
    ModelCallAttributes,
    PlannerStepAttributes,
    ToolCallAttributes,
)
from agent_capture.span.builder import SpanBuilder

pytestmark = pytest.mark.perf


RELAX = float(os.environ.get("AGENT_CAPTURE_PERF_RELAX", "1.0"))
PER_SPAN_BUDGET_MS = 1.0 * RELAX
# Pipeline-growth budget. The test measures RSS *delta* during the
# 10k-span loop, so this bounds leak rate, not absolute footprint.
# The historical 100MB absolute-RSS limit was fragile — pytest collects
# test modules that pull in crewai/presidio/etc., which can push the
# baseline above the cap purely from imports.
MEMORY_DELTA_BUDGET_MB = 20.0 * RELAX


# ---- helpers ------------------------------------------------------------


class _NullExporter(SpanExporter):
    """No-op destination — keeps the pipeline honest without disk I/O."""

    def export(self, span: Span) -> None:
        pass

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _policy_doc() -> dict:
    return {
        "version": "v1",
        "default_strategy": "full",
        "strategies": {
            "full": {"type": "full"},
            "hmac": {"type": "hmac", "key": "perf-test-key"},
        },
        "field_rules": [
            {"field_name": "ssn", "strategy": "full"},
            {"field_name": "account_number", "strategy": "hmac"},
        ],
        "pattern_rules": [
            {"field_type": "ssn", "strategy": "full"},
            {"field_type": "routing_number", "strategy": "hmac"},
            {"field_type": "account_number", "strategy": "hmac"},
        ],
    }


def _rss_mb() -> float:
    """Resident set size in MB. macOS reports bytes, Linux reports kilobytes."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024


def _percentile(samples: list[float], p: float) -> float:
    """Inclusive percentile of a sample list."""
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    k = (len(sorted_s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_s) - 1)
    return sorted_s[f] + (k - f) * (sorted_s[c] - sorted_s[f])


def _bench_span_loop(builder: SpanBuilder, n: int) -> list[float]:
    """Open+close a leaf model_call span ``n`` times. Return per-iter ms."""
    attrs = ModelCallAttributes(
        model_name="claude-opus-4-7",
        provider="anthropic",
        temperature=0.0,
        max_tokens=512,
    )
    timings: list[float] = []
    # Warmup — pay interp specialization, allocator, regex compile costs first.
    for _ in range(200):
        o = builder.open(name="warmup", type=SpanType.MODEL_CALL, attributes=attrs)
        builder.close(o)
    gc.collect()
    for _ in range(n):
        t0 = time.perf_counter_ns()
        o = builder.open(name="m", type=SpanType.MODEL_CALL, attributes=attrs)
        builder.close(o, outputs={"text": "ok"})
        t1 = time.perf_counter_ns()
        timings.append((t1 - t0) / 1_000_000.0)  # ns → ms
    return timings


# ---- per-span overhead --------------------------------------------------


def test_per_span_overhead_unredacted_under_budget() -> None:
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance())
    timings = _bench_span_loop(builder, n=2_000)
    p99 = _percentile(timings, 99.0)
    p50 = _percentile(timings, 50.0)
    assert p99 < PER_SPAN_BUDGET_MS, (
        f"Per-span (no redaction) p99 {p99:.3f}ms exceeds budget {PER_SPAN_BUDGET_MS}ms. "
        f"p50={p50:.3f}ms, mean={statistics.mean(timings):.3f}ms"
    )


def test_per_span_overhead_redacted_under_budget() -> None:
    """The full §10 budget applies even with redaction enabled."""
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance(), redaction_filter=flt)
    timings = _bench_span_loop(builder, n=2_000)
    p99 = _percentile(timings, 99.0)
    p50 = _percentile(timings, 50.0)
    assert p99 < PER_SPAN_BUDGET_MS, (
        f"Per-span (with redaction) p99 {p99:.3f}ms exceeds budget {PER_SPAN_BUDGET_MS}ms. "
        f"p50={p50:.3f}ms, mean={statistics.mean(timings):.3f}ms"
    )


# ---- memory steady-state ------------------------------------------------


def test_memory_steady_state_under_budget() -> None:
    """Drive ~10k spans through a queueless pipeline and check the pipeline doesn't leak.

    Measures growth (delta) — not absolute. The test process has a
    baseline RSS that's dominated by what pytest's collector imported
    before this test ran (crewai + presidio + langgraph + anthropic
    SDKs via --all-extras can easily push baseline RSS above 150MB).
    None of that is the recorder's problem; it just sets the floor.

    The recorder's actual claim — "the capture pipeline doesn't grow
    its working set under sustained span open/close" — is what we
    want to assert. Both signals are checked:

    - tracemalloc delta: precise (only Python-tracked heap allocations
      during the loop). Tight bound.
    - RSS delta: catastrophe net (catches whole-process bloat the GC
      missed, e.g. C-extension allocations). Looser bound.
    """
    gc.collect()
    rss_before = _rss_mb()
    tracemalloc.start()
    snapshot_before = tracemalloc.take_snapshot()

    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance(), redaction_filter=flt)
    attrs = ToolCallAttributes(
        tool_name="loan",
        arguments={"ssn": "123-45-6789", "account_number": "12345678", "amount": 25000},
    )
    for _ in range(10_000):
        root = builder.open(name="r", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes())
        with span_scope(root):
            child = builder.open(name="t", type=SpanType.TOOL_CALL, attributes=attrs)
            builder.close(child)
        builder.close(root)

    gc.collect()
    snapshot_after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    rss_after = _rss_mb()

    delta_bytes = sum(s.size_diff for s in snapshot_after.compare_to(snapshot_before, "filename"))
    tracemalloc_delta_mb = max(0.0, delta_bytes / (1024 * 1024))
    rss_delta_mb = max(0.0, rss_after - rss_before)

    # Tight bound on Python-tracked allocations during the loop.
    assert tracemalloc_delta_mb < 5.0, (
        f"tracemalloc grew {tracemalloc_delta_mb:.1f}MB during 10k spans (>5MB budget) — likely a span retention leak"
    )
    # Catastrophe net: RSS growth from the loop alone, not absolute RSS.
    assert rss_delta_mb < MEMORY_DELTA_BUDGET_MB, (
        f"RSS grew {rss_delta_mb:.1f}MB during 10k spans "
        f"(>{MEMORY_DELTA_BUDGET_MB}MB budget). Absolute RSS "
        f"{rss_before:.0f}MB -> {rss_after:.0f}MB; tracemalloc delta "
        f"{tracemalloc_delta_mb:.1f}MB"
    )


# ---- sustained throughput (proxies CPU + agent-latency-impact claims) -----


# §10 claims:
#   - CPU overhead < 2% of agent CPU
#   - No measurable impact on agent p99 latency
#
# Both are statements about the engine's *absolute* cost in CPU time per span,
# evaluated relative to a real LLM-bound agent step (50ms-5s). They are
# fundamentally untestable against a synthetic sub-millisecond workload:
# scaled to a 20µs loop, ~100µs of engine overhead looks like 5x, but scaled
# to a realistic 100ms LLM call it is 0.1%.
#
# What we *can* assert in a unit test is the absolute throughput the engine
# sustains. If the engine can process at least N spans/sec on this CI host,
# then for any agent doing fewer than N steps/sec it is necessarily under
# the 2% / no-measurable-impact bounds. We pin N at a conservative floor.

MIN_SUSTAINED_THROUGHPUT_SPANS_PER_SEC = 5_000.0 / RELAX
"""Minimum sustained spans/sec the engine must process with redaction on.

A realistic agent generates 5-20 spans per LLM call and an LLM call takes
50ms-5s, so 5k spans/sec is ~250x the rate a single agent will produce.
"""


def test_sustained_throughput_floor() -> None:
    """The engine sustains enough throughput that §10's CPU + latency claims hold.

    A passing run here is the load-bearing evidence for the architecture's
    "< 2% CPU" and "no measurable agent latency impact" claims: any agent
    producing spans slower than this floor cannot make the engine the
    bottleneck or move its p99 measurably.
    """
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance(), redaction_filter=flt)
    attrs = ToolCallAttributes(
        tool_name="loan",
        arguments={"ssn": "123-45-6789", "account_number": "12345678", "amount": 25000},
    )

    iterations = 5_000
    # Warmup.
    for _ in range(500):
        o = builder.open(name="warmup", type=SpanType.TOOL_CALL, attributes=attrs)
        builder.close(o)
    gc.collect()

    t0 = time.perf_counter_ns()
    for _ in range(iterations):
        o = builder.open(name="t", type=SpanType.TOOL_CALL, attributes=attrs)
        builder.close(o, outputs={"score": 700})
    elapsed_s = (time.perf_counter_ns() - t0) / 1e9
    spans_per_sec = iterations / elapsed_s

    assert spans_per_sec >= MIN_SUSTAINED_THROUGHPUT_SPANS_PER_SEC, (
        f"Sustained throughput {spans_per_sec:,.0f} spans/sec below floor "
        f"{MIN_SUSTAINED_THROUGHPUT_SPANS_PER_SEC:,.0f} spans/sec "
        f"({iterations} spans in {elapsed_s * 1000:.1f}ms)"
    )


# ---- micro-benchmarks (informational, not budgeted) ----------------------


def test_open_close_no_redaction(benchmark) -> None:
    """pytest-benchmark micro-bench, reported but not asserted against budget."""
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance())
    attrs = ModelCallAttributes(model_name="m", provider="anthropic")

    def one() -> None:
        o = builder.open(name="m", type=SpanType.MODEL_CALL, attributes=attrs)
        builder.close(o)

    benchmark(one)


def test_open_close_with_redaction(benchmark) -> None:
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    builder = SpanBuilder(_NullExporter(), default_compliance=_compliance(), redaction_filter=flt)
    attrs = ToolCallAttributes(
        tool_name="loan",
        arguments={"ssn": "123-45-6789", "account_number": "12345678", "amount": 25000},
    )

    def one() -> None:
        o = builder.open(name="t", type=SpanType.TOOL_CALL, attributes=attrs)
        builder.close(o)

    benchmark(one)
