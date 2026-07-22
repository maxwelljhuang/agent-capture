"""TenantRoutingExporter — routes by end_customer_id, never raises into host."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_capture.exporter import TenantRoutingExporter
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import (
    ComplianceMetadata,
    PlannerStepAttributes,
    ProvenanceFields,
    RegulatoryRegime,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass


def _span(tenant: str) -> Span:
    return Span(
        span_id="a" * 16,
        parent_span_id=None,
        trajectory_id="a" * 32,
        name="t",
        type=SpanType.PLANNER_STEP,
        start_time=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=datetime(2026, 6, 1, tzinfo=UTC),
        status=SpanStatus.OK,
        attributes=PlannerStepAttributes(),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="a@0.1",
            end_customer_id=tenant,
            regulatory_regime=[RegulatoryRegime.ECOA],
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(content_hash="0" * 64),
    )


class _Cap(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.flushed = 0
        self.shut = 0

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def flush(self, timeout: float = 5.0) -> None:
        self.flushed += 1

    def shutdown(self, timeout: float = 5.0) -> None:
        self.shut += 1


def test_routes_by_tenant() -> None:
    a, b = _Cap(), _Cap()
    exp = TenantRoutingExporter(lambda t: {"acme": a, "demo": b}.get(t))
    exp.export(_span("acme"))
    exp.export(_span("demo"))
    exp.export(_span("acme"))
    assert len(a.spans) == 2
    assert len(b.spans) == 1


def test_unknown_tenant_dropped_not_raised() -> None:
    exp = TenantRoutingExporter(lambda t: None)
    exp.export(_span("nobody"))  # must not raise
    exp.export(_span("nobody"))
    assert exp.dropped_count == 2


def test_factory_called_once_per_tenant() -> None:
    calls: list[str] = []

    def factory(t: str) -> SpanExporter:
        calls.append(t)
        return _Cap()

    exp = TenantRoutingExporter(factory)
    exp.export(_span("acme"))
    exp.export(_span("acme"))
    assert calls == ["acme"]  # cached after first build


def test_flush_and_shutdown_fan_out() -> None:
    a, b = _Cap(), _Cap()
    exp = TenantRoutingExporter(lambda t: {"acme": a, "demo": b}.get(t))
    exp.export(_span("acme"))
    exp.export(_span("demo"))
    exp.flush()
    exp.shutdown()
    assert a.flushed == 1
    assert b.flushed == 1
    assert a.shut == 1
    assert b.shut == 1


def test_inner_exporter_raise_never_escapes() -> None:
    class _Boom(SpanExporter):
        def export(self, span: Span) -> None:
            raise RuntimeError("boom")

        def shutdown(self, timeout: float = 5.0) -> None:
            raise RuntimeError("boom")

    exp = TenantRoutingExporter(lambda t: _Boom())
    exp.export(_span("acme"))  # must not raise
    exp.shutdown()  # must not raise


def test_factory_raise_treated_as_no_exporter() -> None:
    def factory(t: str) -> SpanExporter:
        raise ValueError("nope")

    exp = TenantRoutingExporter(factory)
    exp.export(_span("acme"))  # must not raise
    assert exp.dropped_count == 1


def test_from_tokens_routes_and_drops_unknown() -> None:
    exp = TenantRoutingExporter.from_tokens("http://localhost:1/spans", {"acme": "tok"})
    exp.export(_span("ghost"))  # no token → drop
    assert exp.dropped_count == 1
    exp.export(_span("acme"))  # builds a real pipeline lazily (enqueue only)
    assert exp.dropped_count == 1
    exp.shutdown(timeout=0.5)  # drains; unreachable endpoint logged, never raises
