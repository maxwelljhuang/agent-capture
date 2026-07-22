"""LedgerClient + ledger loaders, using httpx.MockTransport (no live server)."""

from __future__ import annotations

import json
from collections import defaultdict

import httpx
import pytest
from agent_capture.schema import Span

from agent_capture_reporter.common.corpus import ReportingPeriod
from agent_capture_reporter.common.ledger_source import (
    LedgerClient,
    load_corpus_from_ledger,
    load_trajectory_from_ledger,
)
from agent_capture_reporter.errors import TrajectoryLoadError

PERIOD = ReportingPeriod.parse("2026-01-01:2026-03-31")


def _spans_by_trajectory(trajectories: list[list[Span]]) -> dict[str, list[Span]]:
    by_tid: dict[str, list[Span]] = defaultdict(list)
    for spans in trajectories:
        for s in spans:
            by_tid[s.trajectory_id].append(s)
    return dict(by_tid)


def _client(handler) -> LedgerClient:
    transport = httpx.MockTransport(handler)
    return LedgerClient("http://ledger", "tok", client=httpx.Client(transport=transport, base_url="http://ledger"))


def _spans_handler(by_tid: dict[str, list[Span]], *, pages: list[dict] | None = None):
    """Build a request handler serving /trajectories[/{id}/spans]."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/trajectories":
            cursor = request.url.params.get("cursor")
            page = (pages or [{"items": [], "next_cursor": None}])[int(cursor or 0)]
            return httpx.Response(200, json=page)
        if path.endswith("/spans"):
            tid = path.split("/")[2]
            spans = by_tid.get(tid, [])
            body = {"trajectory_id": tid, "spans": [json.loads(s.model_dump_json()) for s in spans]}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"detail": "not found"})

    return handler


def test_get_trajectory_spans_rehydrates_and_builds_trajectory(loan_denial_spans: list[Span]) -> None:
    by_tid = _spans_by_trajectory([loan_denial_spans])
    tid = loan_denial_spans[0].trajectory_id
    with _client(_spans_handler(by_tid)) as client:
        traj = load_trajectory_from_ledger(client, tid)
    assert len(traj) == 7
    assert traj.trajectory_id == tid


def test_sends_bearer_token(loan_denial_spans: list[Span]) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        tid = loan_denial_spans[0].trajectory_id
        return httpx.Response(
            200, json={"trajectory_id": tid, "spans": [json.loads(s.model_dump_json()) for s in loan_denial_spans]}
        )

    with _client(handler) as client:
        load_trajectory_from_ledger(client, loan_denial_spans[0].trajectory_id)
    assert seen["auth"] == "Bearer tok"


def test_list_trajectory_ids_follows_cursor() -> None:
    pages = [
        {"items": [{"trajectory_id": "a" * 32}, {"trajectory_id": "b" * 32}], "next_cursor": "1"},
        {"items": [{"trajectory_id": "c" * 32}], "next_cursor": None},
    ]
    with _client(_spans_handler({}, pages=pages)) as client:
        ids = client.list_trajectory_ids(frm=PERIOD.start, to=PERIOD.end)
    assert ids == ["a" * 32, "b" * 32, "c" * 32]


def test_load_corpus_from_ledger(inventory_corpus: list[list[Span]]) -> None:
    by_tid = _spans_by_trajectory(inventory_corpus)
    tids = list(by_tid)
    pages = [{"items": [{"trajectory_id": t} for t in tids], "next_cursor": None}]
    with _client(_spans_handler(by_tid, pages=pages)) as client:
        corpus = load_corpus_from_ledger(client, PERIOD)
    assert len(corpus) == len(tids)


def test_empty_period_yields_empty_corpus() -> None:
    pages = [{"items": [], "next_cursor": None}]
    with _client(_spans_handler({}, pages=pages)) as client:
        corpus = load_corpus_from_ledger(client, PERIOD)
    assert len(corpus) == 0


def test_http_error_raises_trajectory_load_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauthorized"})

    with _client(handler) as client, pytest.raises(TrajectoryLoadError, match="401"):
        load_trajectory_from_ledger(client, "a" * 32)


def test_unknown_trajectory_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"trajectory_id": "x" * 32, "spans": []})

    with _client(handler) as client, pytest.raises(TrajectoryLoadError, match="no spans"):
        load_trajectory_from_ledger(client, "x" * 32)
