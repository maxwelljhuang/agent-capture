"""Hash recompute matches the recorder byte-for-byte."""

from __future__ import annotations

from agent_capture.schema.canonical import content_hash

from agent_capture_ledger.integrity.verifier import check_content_hash, recompute_content_hash
from tests._helpers import make_span


def test_recompute_matches_recorder() -> None:
    s = make_span()
    assert recompute_content_hash(s) == s.provenance.content_hash
    assert recompute_content_hash(s) == content_hash(s)


def test_check_ok_when_unchanged() -> None:
    s = make_span()
    chk = check_content_hash(s)
    assert chk.ok
    assert chk.expected == chk.computed


def test_check_fails_when_provenance_lies() -> None:
    s = make_span()
    fake = s.model_copy(
        update={
            "provenance": s.provenance.model_copy(update={"content_hash": "0" * 64}),
        }
    )
    chk = check_content_hash(fake)
    assert not chk.ok
    assert chk.expected == "0" * 64
    assert chk.computed == s.provenance.content_hash
