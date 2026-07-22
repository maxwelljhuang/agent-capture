"""Single-span integrity checks.

The ledger NEVER reimplements canonicalization — drift between writer
(recorder) and reader (ledger) is the one bug that would invalidate every
guarantee the product makes. We import the recorder's canonical module
directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_capture.schema import Span
from agent_capture.schema.canonical import content_hash as _canonical_hash


@dataclass(frozen=True)
class HashCheck:
    ok: bool
    expected: str
    computed: str


def recompute_content_hash(span: Span) -> str:
    """Recompute the canonical SHA-256 of ``span``.

    Excludes the ``provenance`` block per the canonical contract.
    """
    return _canonical_hash(span)


def check_content_hash(span: Span) -> HashCheck:
    computed = recompute_content_hash(span)
    return HashCheck(
        ok=computed == span.provenance.content_hash, expected=span.provenance.content_hash, computed=computed
    )
