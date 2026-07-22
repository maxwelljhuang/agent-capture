"""Argon2 hashing roundtrips."""

from __future__ import annotations

from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
    verify_secret,
)


def test_secret_roundtrip() -> None:
    secret = generate_secret()
    h = hash_secret(secret)
    assert verify_secret(secret, h)
    assert not verify_secret(secret + "x", h)
    assert not verify_secret("", h)


def test_token_id_shape() -> None:
    tid = generate_token_id()
    assert tid.startswith("tok_")
    assert len(tid) > len("tok_")


def test_each_hash_is_distinct() -> None:
    h1 = hash_secret("same")
    h2 = hash_secret("same")
    # Argon2 salts independently — even same input differs
    assert h1 != h2
    assert verify_secret("same", h1)
    assert verify_secret("same", h2)
