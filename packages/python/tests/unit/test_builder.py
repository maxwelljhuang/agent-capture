"""SpanBuilder tests — hash chain, parent-child wiring, never-raises."""

from __future__ import annotations

from agent_capture.context.propagation import span_scope
from agent_capture.exporter.base import SpanExporter
from agent_capture.schema import (
    ComplianceMetadata,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import (
    ModelCallAttributes,
    PlannerStepAttributes,
    SideEffectAttributes,
    ToolCallAttributes,
)
from agent_capture.span.builder import SpanBuilder


class _CaptureExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

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


def _builder() -> tuple[SpanBuilder, _CaptureExporter]:
    exp = _CaptureExporter()
    return SpanBuilder(exp, default_compliance=_compliance()), exp


def test_root_span_ships_immediately_no_parent_hash() -> None:
    b, exp = _builder()
    open_root = b.open(
        name="root",
        type=SpanType.PLANNER_STEP,
        attributes=PlannerStepAttributes(),
    )
    b.close(open_root, outputs={"ok": True})

    assert len(exp.spans) == 1
    root = exp.spans[0]
    assert root.parent_span_id is None
    assert root.provenance.parent_content_hash is None
    assert root.provenance.content_hash


def test_child_buffers_until_parent_closes_then_chain_links() -> None:
    b, exp = _builder()
    open_root = b.open(name="root", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes())
    with span_scope(open_root):
        open_child = b.open(
            name="model",
            type=SpanType.MODEL_CALL,
            attributes=ModelCallAttributes(model_name="claude-opus-4-7", provider="anthropic"),
        )
        b.close(open_child, outputs={"text": "ok"})
        # Child has been finalized but NOT yet shipped — it's waiting for the parent.
        assert exp.spans == []
    b.close(open_root)

    # Now both are shipped — child first (leaf), then root.
    assert len(exp.spans) == 2
    child, root = exp.spans
    assert root.parent_span_id is None
    assert root.provenance.parent_content_hash is None
    assert child.parent_span_id == root.span_id
    assert child.trajectory_id == root.trajectory_id
    assert child.provenance.parent_content_hash == root.provenance.content_hash


def test_deep_tree_chains_through_three_levels() -> None:
    b, exp = _builder()
    open_root = b.open(name="root", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes())
    with span_scope(open_root):
        open_mid = b.open(name="mid", type=SpanType.SUB_AGENT_INVOCATION, attributes=PlannerStepAttributes())
        # Note: this span deliberately violates kind/type match — close() will fail validation
        # internally and the hash should not be ditched anywhere weird. Use a matching type:
        open_mid = b.open(
            name="mid",
            type=SpanType.PLANNER_STEP,
            attributes=PlannerStepAttributes(),
        )
        with span_scope(open_mid):
            open_leaf = b.open(
                name="leaf",
                type=SpanType.TOOL_CALL,
                attributes=ToolCallAttributes(tool_name="x"),
            )
            b.close(open_leaf)
        b.close(open_mid)
    b.close(open_root)

    # Order: deepest first.
    names_in_export_order = [s.name for s in exp.spans]
    assert names_in_export_order == ["leaf", "mid", "root"]
    leaf, mid, root = exp.spans
    assert leaf.provenance.parent_content_hash == mid.provenance.content_hash
    assert mid.provenance.parent_content_hash == root.provenance.content_hash
    assert root.provenance.parent_content_hash is None


def test_close_never_raises_on_validation_failure() -> None:
    """A schema violation must not propagate. close() returns None and logs."""
    b, _ = _builder()
    open_span = b.open(name="bad", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes())
    # type/attributes mismatch will trigger ValidationError inside close().
    open_span.type = SpanType.TOOL_CALL  # type: ignore[assignment]
    result = b.close(open_span)
    assert result is None


def test_critical_span_type_preserved_through_close() -> None:
    b, exp = _builder()
    open_root = b.open(name="root", type=SpanType.PLANNER_STEP, attributes=PlannerStepAttributes())
    with span_scope(open_root):
        open_se = b.open(
            name="send_letter",
            type=SpanType.SIDE_EFFECT,
            attributes=SideEffectAttributes(
                action_type="email.send",
                target_system="acme.mail",
                success=True,
            ),
        )
        b.close(open_se)
    b.close(open_root)

    side_effects = [s for s in exp.spans if s.type is SpanType.SIDE_EFFECT]
    assert len(side_effects) == 1
    assert side_effects[0].status is SpanStatus.OK
