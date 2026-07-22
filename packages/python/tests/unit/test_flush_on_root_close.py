"""flush_on_root_close (serverless mode) — flush once when the root span closes."""

from __future__ import annotations

from agent_capture.schema import (
    ComplianceMetadata,
    PlannerStepAttributes,
    RegulatoryRegime,
    Span,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.span.builder import SpanBuilder


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="a@0.1",
        end_customer_id="acme",
        regulatory_regime=[RegulatoryRegime.ECOA],
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


class _Rec:
    def __init__(self) -> None:
        self.exported: list[Span] = []
        self.flushes = 0

    def export(self, span: Span) -> None:
        self.exported.append(span)

    def flush(self, timeout: float = 5.0) -> None:
        self.flushes += 1

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def _builder(exporter: object, flush_on_root_close: bool) -> SpanBuilder:
    return SpanBuilder(exporter, default_compliance=_compliance(), flush_on_root_close=flush_on_root_close)  # type: ignore[arg-type]


def _open(b: SpanBuilder, parent=None):  # type: ignore[no-untyped-def]
    return b.open(name="s", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes(), parent=parent)


def test_flush_once_on_root_close_not_on_child() -> None:
    rec = _Rec()
    b = _builder(rec, flush_on_root_close=True)
    root = _open(b)
    child = _open(b, parent=root)
    b.close(child)
    assert rec.flushes == 0  # child close does not flush
    b.close(root)
    assert rec.flushes == 1  # exactly one flush, on root close


def test_no_flush_when_disabled() -> None:
    rec = _Rec()
    b = _builder(rec, flush_on_root_close=False)
    b.close(_open(b))
    assert rec.flushes == 0


def test_exporter_without_flush_is_safe() -> None:
    class _NoFlush:
        def __init__(self) -> None:
            self.exported: list[Span] = []

        def export(self, span: Span) -> None:
            self.exported.append(span)

        def shutdown(self, timeout: float = 5.0) -> None:
            pass

    b = _builder(_NoFlush(), flush_on_root_close=True)
    b.close(_open(b))  # getattr guard → no error


def test_flush_raise_never_escapes() -> None:
    class _Boom:
        def export(self, span: Span) -> None:
            pass

        def flush(self, timeout: float = 5.0) -> None:
            raise RuntimeError("boom")

        def shutdown(self, timeout: float = 5.0) -> None:
            pass

    b = _builder(_Boom(), flush_on_root_close=True)
    b.close(_open(b))  # guarded → must not raise
