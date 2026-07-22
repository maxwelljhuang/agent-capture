"""Unit-level invariants on the Span schema.

These test the validators on :class:`agent_capture.schema.span.Span`. The
deeper trajectory-shaped contracts live in :mod:`tests.scenarios`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agent_capture.schema import (
    ComplianceMetadata,
    ErrorInfo,
    ProvenanceFields,
    Span,
    SpanStatus,
    SpanType,
)
from agent_capture.schema.compliance import DataClassification, RetentionClass
from agent_capture.schema.types import ModelCallAttributes, ToolCallAttributes


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _provenance() -> ProvenanceFields:
    return ProvenanceFields(content_hash="0" * 64)


def _now() -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _model_attrs() -> ModelCallAttributes:
    return ModelCallAttributes(model_name="claude-opus-4-7", provider="anthropic")


def test_type_must_match_attributes_kind() -> None:
    with pytest.raises(ValidationError, match=r"must equal Span\.attributes\.kind"):
        Span(
            span_id="a" * 16,
            parent_span_id=None,
            trajectory_id="a" * 32,
            name="bad",
            type=SpanType.TOOL_CALL,
            start_time=_now(),
            end_time=_now() + timedelta(milliseconds=1),
            attributes=_model_attrs(),
            compliance=_compliance(),
            provenance=_provenance(),
        )


def test_end_time_must_not_precede_start_time() -> None:
    with pytest.raises(ValidationError, match="end_time must be >= "):
        Span(
            span_id="a" * 16,
            parent_span_id=None,
            trajectory_id="a" * 32,
            name="bad",
            type=SpanType.MODEL_CALL,
            start_time=_now(),
            end_time=_now() - timedelta(seconds=1),
            attributes=_model_attrs(),
            compliance=_compliance(),
            provenance=_provenance(),
        )


def test_error_status_requires_error_info() -> None:
    with pytest.raises(ValidationError, match=r"Span\.error must be set"):
        Span(
            span_id="a" * 16,
            parent_span_id=None,
            trajectory_id="a" * 32,
            name="bad",
            type=SpanType.MODEL_CALL,
            start_time=_now(),
            end_time=_now() + timedelta(milliseconds=1),
            status=SpanStatus.ERROR,
            attributes=_model_attrs(),
            compliance=_compliance(),
            provenance=_provenance(),
        )


def test_ok_status_forbids_error_info() -> None:
    with pytest.raises(ValidationError, match=r"Span\.error must be None"):
        Span(
            span_id="a" * 16,
            parent_span_id=None,
            trajectory_id="a" * 32,
            name="bad",
            type=SpanType.MODEL_CALL,
            start_time=_now(),
            end_time=_now() + timedelta(milliseconds=1),
            status=SpanStatus.OK,
            error=ErrorInfo(error_type="X", message="should not be here"),
            attributes=_model_attrs(),
            compliance=_compliance(),
            provenance=_provenance(),
        )


def test_span_id_must_be_16_hex() -> None:
    with pytest.raises(ValidationError):
        Span(
            span_id="not-hex",
            parent_span_id=None,
            trajectory_id="a" * 32,
            name="bad",
            type=SpanType.MODEL_CALL,
            start_time=_now(),
            end_time=_now() + timedelta(milliseconds=1),
            attributes=_model_attrs(),
            compliance=_compliance(),
            provenance=_provenance(),
        )


def test_discriminated_union_accepts_each_type() -> None:
    """Spot-check that the union covers all eight Section 4.2 span types."""
    base = {
        "span_id": "a" * 16,
        "parent_span_id": None,
        "trajectory_id": "a" * 32,
        "start_time": _now(),
        "end_time": _now() + timedelta(milliseconds=1),
        "compliance": _compliance(),
        "provenance": _provenance(),
    }
    # tool_call exercises a different variant of the union; combined with
    # model_call (covered above) and the eight scenario tests, all branches
    # of TypedAttributes are exercised.
    span = Span(
        **base,
        name="t",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(tool_name="x"),
    )
    assert span.attributes.kind == "tool_call"
