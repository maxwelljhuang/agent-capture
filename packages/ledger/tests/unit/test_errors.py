"""ProblemDetails error shape."""

from __future__ import annotations

from agent_capture_ledger.api.errors import LE001, LE003, problem_response


def test_problem_includes_code_and_type_uri() -> None:
    body = LE001.problem("bad field")
    assert body["code"] == "LE001"
    assert body["status"] == 422
    assert body["type"].endswith("/LE001")
    assert body["title"] == "Span shape invalid"
    assert body["detail"] == "bad field"


def test_problem_response_uses_problem_media_type() -> None:
    r = problem_response(LE003, "hash mismatch")
    assert r.media_type == "application/problem+json"
    assert r.status_code == 422
