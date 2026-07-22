"""W3C Trace Context inject/extract tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_capture.context.propagation import span_scope
from agent_capture.context.w3c import (
    TRACEPARENT_HEADER,
    RemoteParent,
    extract,
    inject,
)
from agent_capture.schema import SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import PlannerStepAttributes
from agent_capture.span.builder import OpenSpan


def _open_span(span_id: str, trajectory_id: str) -> OpenSpan:
    return OpenSpan(
        span_id=span_id,
        parent_span_id=None,
        trajectory_id=trajectory_id,
        name="x",
        type=SpanType.PLANNER_STEP,
        start_time=datetime(2026, 5, 17, tzinfo=UTC),
        attributes=PlannerStepAttributes(),
        compliance=ComplianceMetadata(
            policy_version_active="v1",
            agent_version="0.1.0",
            end_customer_id="acme",
            retention_class=RetentionClass.STANDARD,
            data_classification=DataClassification.INTERNAL,
        ),
    )


def test_inject_returns_empty_outside_scope() -> None:
    assert inject() == {}


def test_inject_emits_well_formed_traceparent() -> None:
    parent = _open_span("0123456789abcdef", "0123456789abcdef" * 2)
    with span_scope(parent):
        headers = inject()
    tp = headers[TRACEPARENT_HEADER]
    assert tp == "00-" + ("0123456789abcdef" * 2) + "-0123456789abcdef-01"


def test_inject_respects_unsampled_flag() -> None:
    parent = _open_span("aaaaaaaaaaaaaaaa", "b" * 32)
    with span_scope(parent):
        headers = inject(sampled=False)
    assert headers[TRACEPARENT_HEADER].endswith("-00")


def test_extract_roundtrips_inject() -> None:
    parent = _open_span("aaaaaaaaaaaaaaaa", "0123456789abcdef" * 2)
    with span_scope(parent):
        headers = inject()
    remote = extract(headers)
    assert remote == RemoteParent(
        trajectory_id="0123456789abcdef" * 2,
        span_id="aaaaaaaaaaaaaaaa",
        sampled=True,
    )


@pytest.mark.parametrize(
    "header",
    [
        "",
        "not-a-traceparent",
        "00-too-short-1234567890abcdef-01",
        "ff-" + "a" * 32 + "-" + "a" * 16 + "-01",  # reserved version
    ],
)
def test_extract_returns_none_for_malformed(header: str) -> None:
    assert extract({"traceparent": header}) is None


def test_extract_is_case_insensitive() -> None:
    parent = _open_span("aaaaaaaaaaaaaaaa", "a" * 32)
    with span_scope(parent):
        headers = inject()
    upper = {k.upper(): v for k, v in headers.items()}
    assert extract(upper) is not None


def test_extract_returns_none_when_header_absent() -> None:
    assert extract({}) is None
