"""Test helpers: build valid Span instances quickly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from agent_capture.schema import (
    ComplianceMetadata,
    PlannerStepAttributes,
    ProvenanceFields,
    RegulatoryRegime,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import DataClassification, RetentionClass


def _ids() -> tuple[str, str]:
    return uuid.uuid4().hex[:16], uuid.uuid4().hex


def make_span(
    *,
    span_id: str | None = None,
    trajectory_id: str | None = None,
    parent_span_id: str | None = None,
    parent_content_hash: str | None = None,
    end_customer_id: str = "demo-co",
    regime: list[RegulatoryRegime] | None = None,
    retention: RetentionClass = RetentionClass.STANDARD,
    started_at: datetime | None = None,
    name: str = "test-span",
    span_type: SpanType = SpanType.PLANNER_STEP,
    attributes: Any | None = None,
    status: SpanStatus = SpanStatus.OK,
    subject_id: str | None = None,
) -> Span:
    """Build a Span with a valid content_hash.

    Defaults to a ``planner_step``; pass ``span_type`` + matching ``attributes``
    (e.g. ``PolicyCheckAttributes``) to build other span types.
    """
    span_id = span_id or uuid.uuid4().hex[:16]
    trajectory_id = trajectory_id or uuid.uuid4().hex
    start = started_at or datetime.now(UTC)
    end = start
    s = Span(
        span_id=span_id,
        parent_span_id=parent_span_id,
        trajectory_id=trajectory_id,
        name=name,
        type=span_type,
        start_time=start,
        end_time=end,
        status=status,
        error=None,
        inputs=None,
        outputs=None,
        attributes=attributes if attributes is not None else PlannerStepAttributes(),
        compliance=ComplianceMetadata(
            policy_version_active="test-policy@1",
            agent_version="test-agent@0.1",
            end_customer_id=end_customer_id,
            subject_id=subject_id,
            regulatory_regime=regime or [RegulatoryRegime.GDPR],
            retention_class=retention,
            data_classification=DataClassification.INTERNAL,
        ),
        provenance=ProvenanceFields(
            content_hash="0" * 64,
            parent_content_hash=parent_content_hash,
            schema_version="1.0.0",
        ),
    )
    h = content_hash(s)
    # rebuild with computed hash
    s2 = s.model_copy(
        update={
            "provenance": ProvenanceFields(
                content_hash=h,
                parent_content_hash=parent_content_hash,
                schema_version="1.0.0",
            ),
        }
    )
    return s2


def envelope(spans):
    return {"spans": [s.model_dump(mode="json") for s in spans]}
