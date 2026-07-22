"""Merkle tree round-trips for every leaf-count regime."""

from __future__ import annotations

import hashlib

import pytest

from agent_capture_ledger.integrity.merkle import (
    build_proof,
    build_root,
    trajectory_root,
    verify_proof,
)


def H(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_single_leaf_root_is_leaf() -> None:
    leaf = H("only")
    assert build_root([leaf]) == leaf


def test_two_leaves() -> None:
    a, b = H("a"), H("b")
    root = build_root([a, b])
    assert root == hashlib.sha256((a + b).encode()).hexdigest()


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 33])
def test_proof_round_trips_for_every_leaf(n) -> None:
    leaves = [H(f"x{i}") for i in range(n)]
    root = build_root(leaves)
    for i in range(n):
        proof = build_proof(leaves, i)
        assert proof.root == root
        assert verify_proof(proof)


def test_tampered_leaf_breaks_proof() -> None:
    leaves = [H(f"x{i}") for i in range(8)]
    proof = build_proof(leaves, 3)
    bad = proof.__class__(
        leaf=H("evil"), leaf_index=proof.leaf_index, leaf_count=proof.leaf_count, root=proof.root, path=proof.path
    )
    assert not verify_proof(bad)


def test_trajectory_root_is_merkle_over_content_hashes() -> None:
    hashes = [H(f"span{i}") for i in range(5)]
    assert trajectory_root(hashes) == build_root(hashes)
