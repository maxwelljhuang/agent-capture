"""Helpers for building trajectory trees in scenario tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agent_capture.schema import (
    ComplianceMetadata,
    ErrorInfo,
    ProvenanceFields,
    RegulatoryRegime,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import TypedAttributes


def build_span(
    *,
    span_id: str,
    trajectory_id: str,
    parent_span_id: str | None,
    name: str,
    type: SpanType,
    attributes: TypedAttributes,
    start_time: datetime,
    duration_ms: int = 10,
    inputs: Any | None = None,
    outputs: Any | None = None,
    status: SpanStatus = SpanStatus.OK,
    error: ErrorInfo | None = None,
    compliance: ComplianceMetadata | None = None,
    parent_content_hash: str | None = None,
) -> Span:
    """Build a fully-populated Span with a computed content hash.

    This mirrors what the span builder will do at runtime in Week 2: build
    the span without a hash, compute the hash over the canonical form, then
    attach the hash to the provenance block.
    """
    end_time = start_time + timedelta(milliseconds=duration_ms)
    metadata = compliance or ComplianceMetadata(
        policy_version_active="v1.0.0",
        agent_version="0.1.0",
        end_customer_id="acme-bank",
        regulatory_regime=[RegulatoryRegime.ECOA, RegulatoryRegime.FCRA],
        retention_class=RetentionClass.EXTENDED,
        data_classification=DataClassification.PII,
    )
    placeholder = Span(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trajectory_id=trajectory_id,
        name=name,
        type=type,
        start_time=start_time,
        end_time=end_time,
        status=status,
        error=error,
        attributes=attributes,
        inputs=inputs,
        outputs=outputs,
        compliance=metadata,
        provenance=ProvenanceFields(
            content_hash="0" * 64,
            parent_content_hash=parent_content_hash,
        ),
    )
    real_hash = content_hash(placeholder)
    return placeholder.model_copy(
        update={
            "provenance": ProvenanceFields(
                content_hash=real_hash,
                parent_content_hash=parent_content_hash,
            )
        }
    )


def utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    """Short helper for UTC datetimes."""
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def assert_trajectory_well_formed(trajectory: list[Span]) -> None:
    """Assert structural invariants every trajectory must satisfy.

    1. Exactly one root (parent_span_id is None).
    2. All spans share the same trajectory_id.
    3. Every non-root span's parent_span_id references a span in the list.
    4. Every non-root span's provenance.parent_content_hash equals its parent's content_hash.
    5. Every span's compliance and provenance blocks are populated.
    """
    assert trajectory, "trajectory must be non-empty"

    roots = [s for s in trajectory if s.parent_span_id is None]
    assert len(roots) == 1, f"expected exactly one root, got {len(roots)}"

    trajectory_ids = {s.trajectory_id for s in trajectory}
    assert len(trajectory_ids) == 1, f"all spans must share a trajectory_id, got {trajectory_ids}"

    by_id = {s.span_id: s for s in trajectory}
    assert len(by_id) == len(trajectory), "duplicate span_ids in trajectory"

    for s in trajectory:
        if s.parent_span_id is None:
            assert s.provenance.parent_content_hash is None
            continue
        assert s.parent_span_id in by_id, f"dangling parent_span_id={s.parent_span_id!r}"
        parent = by_id[s.parent_span_id]
        assert s.provenance.parent_content_hash == parent.provenance.content_hash, (
            f"chain broken at span {s.span_id}: "
            f"expected parent hash {parent.provenance.content_hash[:12]}..., "
            f"got {s.provenance.parent_content_hash!r}"
        )


def assert_reporting_fields_populated(span: Span) -> None:
    """Assert every Section 4.3 + 4.4 field the reporting layer will need is set.

    Reporting downstream cannot reconstruct these. If any are missing on a span
    that needs them, that's a Week 1 schema bug, not a downstream bug.
    """
    c = span.compliance
    assert c.policy_version_active
    assert c.agent_version
    assert c.end_customer_id
    assert c.retention_class is not None
    assert c.data_classification is not None

    p = span.provenance
    assert p.content_hash
    assert p.schema_version

    # The model_call-specific compliance metadata only applies to model_call spans.
    if span.type is SpanType.MODEL_CALL:
        assert c.model_card_version is not None or c.prompt_template_version is not None, (
            "model_call spans must record at least one of model_card_version or prompt_template_version "
            "for adverse-action reporting"
        )
