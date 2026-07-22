"""BoundedQueueExporter tests — drop policy + critical-span discipline."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.queue import BoundedQueueExporter
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    PlannerStepAttributes,
    SideEffectAttributes,
)


def _make_span(i: int, type: SpanType = SpanType.PLANNER_STEP) -> Span:
    if type is SpanType.SIDE_EFFECT:
        attrs = SideEffectAttributes(action_type="x", target_system="y", success=True)
    else:
        attrs = PlannerStepAttributes()
    return Span(
        span_id=f"{i:016x}",
        parent_span_id=None,
        trajectory_id=f"{i:032x}",
        name=f"s{i}",
        type=type,
        start_time=datetime(2026, 5, 17, tzinfo=UTC),
        end_time=datetime(2026, 5, 17, tzinfo=UTC),
        attributes=attrs,
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(content_hash="0" * 64),
    )


class _CollectingExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self._lock = threading.Lock()

    def export(self, span: Span) -> None:
        with self._lock:
            self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


class _BlockingExporter(SpanExporter):
    """Inner exporter that blocks until released — for saturating the queue."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.seen: list[Span] = []

    def export(self, span: Span) -> None:
        self.release.wait()
        self.seen.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.release.set()


def test_basic_drain_passes_through_to_inner() -> None:
    inner = _CollectingExporter()
    q = BoundedQueueExporter(inner, max_size=16)
    for i in range(5):
        q.export(_make_span(i))
    q.shutdown(timeout=2.0)
    assert len(inner.spans) == 5


def test_non_critical_drops_when_queue_saturated() -> None:
    inner = _BlockingExporter()
    q = BoundedQueueExporter(inner, max_size=2, critical_block_timeout=0.05)

    # First span starts draining and blocks the worker. Fill the queue beyond capacity.
    for i in range(20):
        q.export(_make_span(i, type=SpanType.PLANNER_STEP))

    assert q.dropped_count > 0, "expected drops once queue saturated"

    inner.release.set()
    q.shutdown(timeout=2.0)


def test_critical_span_blocks_briefly_then_drops_loudly() -> None:
    """A side_effect span must NOT be silently dropped — wait first, then loud drop."""
    inner = _BlockingExporter()
    q = BoundedQueueExporter(inner, max_size=1, critical_block_timeout=0.1)

    # Fill queue with worker blocked.
    q.export(_make_span(0))  # this one will sit in the worker, blocked
    time.sleep(0.05)
    q.export(_make_span(1))  # fills the queue (worker still blocked on #0)

    start = time.monotonic()
    q.export(_make_span(2, type=SpanType.SIDE_EFFECT))  # critical — should block
    elapsed = time.monotonic() - start

    assert elapsed >= 0.09, f"critical span should have blocked at least ~timeout, got {elapsed}s"
    # And the drop should have been recorded loudly.
    assert q.dropped_count >= 1

    inner.release.set()
    q.shutdown(timeout=2.0)


def test_shutdown_is_idempotent() -> None:
    inner = _CollectingExporter()
    q = BoundedQueueExporter(inner, max_size=4)
    q.export(_make_span(0))
    q.shutdown(timeout=1.0)
    q.shutdown(timeout=1.0)  # no error


def test_export_after_shutdown_increments_drop_counter() -> None:
    inner = _CollectingExporter()
    q = BoundedQueueExporter(inner, max_size=4)
    q.shutdown(timeout=1.0)
    q.export(_make_span(0))
    assert q.dropped_count == 1
