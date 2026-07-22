"""Binary Merkle tree over SHA-256 leaf hashes.

Deterministic for any leaf count. Odd nodes are promoted unchanged (no
duplicate-leaf attacks because all our leaves are SHA-256 of structured
data, never user input). Returns a typed proof for any leaf index.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ProofStep:
    sibling: str  # hex SHA-256
    side: Literal["L", "R"]  # which side the sibling is on relative to current


@dataclass(frozen=True)
class MerkleProof:
    leaf: str
    leaf_index: int
    leaf_count: int
    root: str
    path: list[ProofStep]


def _h2(a: str, b: str) -> str:
    return hashlib.sha256((a + b).encode("ascii")).hexdigest()


def build_root(leaves: list[str]) -> str:
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        nxt: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_h2(level[i], level[i + 1]))
            else:
                nxt.append(level[i])  # promote odd
        level = nxt
    return level[0]


def build_proof(leaves: list[str], idx: int) -> MerkleProof:
    if not leaves:
        raise ValueError("empty leaves")
    if not 0 <= idx < len(leaves):
        raise ValueError(f"idx {idx} out of range for {len(leaves)} leaves")

    path: list[ProofStep] = []
    level = list(leaves)
    cur = idx
    while len(level) > 1:
        sibling_idx = cur ^ 1
        if sibling_idx < len(level):
            sibling = level[sibling_idx]
            side: Literal["L", "R"] = "L" if sibling_idx < cur else "R"
            path.append(ProofStep(sibling=sibling, side=side))
        # ... else: cur was the odd promoted node, no sibling at this level

        nxt: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_h2(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        level = nxt
        cur //= 2

    return MerkleProof(
        leaf=leaves[idx],
        leaf_index=idx,
        leaf_count=len(leaves),
        root=level[0],
        path=path,
    )


def verify_proof(proof: MerkleProof) -> bool:
    h = proof.leaf
    for step in proof.path:
        h = _h2(step.sibling, h) if step.side == "L" else _h2(h, step.sibling)
    return h == proof.root


def trajectory_root(span_content_hashes: list[str]) -> str:
    """Trajectory root = Merkle root of the spans' content_hashes in order."""
    return build_root(span_content_hashes)
