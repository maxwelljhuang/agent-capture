"""Hot-path budgets for hash recompute + Merkle build.

These run without a DB. The ingest path's per-span work is dominated by
``content_hash`` recompute; Merkle building dominates the anchor pass.
Both must be fast enough that a single ledger node can sustain
~1000 spans/s with headroom.
"""

from __future__ import annotations

import hashlib
import time

import pytest
from agent_capture.schema.canonical import content_hash

from agent_capture_ledger.integrity import merkle
from tests._helpers import make_span

pytestmark = pytest.mark.perf


def test_content_hash_under_300us_per_span() -> None:
    """Per-span recompute should be well under 1ms; budget is 300µs."""
    spans = [make_span() for _ in range(200)]
    start = time.perf_counter()
    for s in spans:
        content_hash(s)
    elapsed = time.perf_counter() - start
    per_span_us = (elapsed / len(spans)) * 1_000_000
    assert per_span_us < 300, f"content_hash {per_span_us:.0f}µs/span exceeds 300µs budget"


def test_merkle_root_1000_leaves_under_5ms() -> None:
    leaves = [hashlib.sha256(f"x{i}".encode()).hexdigest() for i in range(1000)]
    start = time.perf_counter()
    merkle.build_root(leaves)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 5, f"merkle root 1000 leaves took {elapsed_ms:.1f}ms (>5ms budget)"


def test_merkle_proof_round_trip_under_2ms() -> None:
    leaves = [hashlib.sha256(f"x{i}".encode()).hexdigest() for i in range(1024)]
    start = time.perf_counter()
    for i in range(0, 1024, 32):
        proof = merkle.build_proof(leaves, i)
        assert merkle.verify_proof(proof)
    elapsed_ms = (time.perf_counter() - start) * 1000
    avg_us = (elapsed_ms * 1000) / 32
    assert avg_us < 2000, f"avg proof+verify {avg_us:.0f}µs > 2ms budget"
