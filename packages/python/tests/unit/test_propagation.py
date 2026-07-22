"""Context propagation tests."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from agent_capture.context.propagation import bind_context, current_parent, span_scope
from agent_capture.schema import SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import PlannerStepAttributes
from agent_capture.span.builder import OpenSpan


def _open_span(name: str) -> OpenSpan:
    return OpenSpan(
        span_id="a" * 16,
        parent_span_id=None,
        trajectory_id="a" * 32,
        name=name,
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


def test_baseline_parent_is_none() -> None:
    assert current_parent() is None


def test_span_scope_sets_and_restores_parent() -> None:
    a = _open_span("a")
    b = _open_span("b")
    assert current_parent() is None
    with span_scope(a):
        assert current_parent() is a
        with span_scope(b):
            assert current_parent() is b
        assert current_parent() is a
    assert current_parent() is None


def test_span_scope_restores_on_exception() -> None:
    a = _open_span("a")
    with pytest.raises(RuntimeError), span_scope(a):
        assert current_parent() is a
        raise RuntimeError("boom")
    assert current_parent() is None


async def test_asyncio_create_task_propagates_parent() -> None:
    a = _open_span("a")
    observed: list[OpenSpan | None] = []

    async def inner() -> None:
        observed.append(current_parent())

    with span_scope(a):
        task = asyncio.create_task(inner())
        await task
    assert observed == [a]


def test_thread_pool_needs_explicit_copy() -> None:
    a = _open_span("a")
    observed_with_bind: list[OpenSpan | None] = []
    observed_without_bind: list[OpenSpan | None] = []

    def record(target: list[OpenSpan | None]) -> None:
        target.append(current_parent())

    with span_scope(a), ThreadPoolExecutor(max_workers=2) as pool:
        # Naked submit: contextvar is empty in the worker thread.
        pool.submit(record, observed_without_bind).result()
        # bind_context snapshots the submitter's context.
        pool.submit(bind_context(record), observed_with_bind).result()

    assert observed_without_bind == [None]
    assert observed_with_bind == [a]
