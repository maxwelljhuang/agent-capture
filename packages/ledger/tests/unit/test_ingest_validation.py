"""Ingest validation paths that don't need the DB.

The actual happy/sad paths against Postgres are exercised in the
integration test; here we only verify the envelope model accepts
HTTPExporter-shaped bodies.
"""

from __future__ import annotations

from agent_capture_ledger.api.routes.ingest import SpansEnvelope
from tests._helpers import envelope, make_span


def test_envelope_accepts_recorder_shape() -> None:
    spans = [make_span() for _ in range(3)]
    body = envelope(spans)
    env = SpansEnvelope.model_validate(body)
    assert len(env.spans) == 3
    assert env.spans[0]["span_id"] == spans[0].span_id


def test_envelope_accepts_empty_batch() -> None:
    env = SpansEnvelope.model_validate({"spans": []})
    assert env.spans == []
