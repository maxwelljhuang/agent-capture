"""Argon2 hashing for API tokens.

Tokens have two parts: a public ``token_id`` (looked up in the DB) and a
secret that's only ever shown to the caller at creation time. We store the
Argon2 hash of the secret.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def generate_secret(nbytes: int = 32) -> str:
    """Cryptographically random URL-safe token secret."""
    return secrets.token_urlsafe(nbytes)


def generate_token_id() -> str:
    """Opaque public token identifier. Prefixed for log-grep friendliness."""
    return f"tok_{secrets.token_urlsafe(12)}"


def hash_secret(secret: str) -> str:
    return _hasher.hash(secret)


def verify_secret(secret: str, stored_hash: str) -> bool:
    try:
        return _hasher.verify(stored_hash, secret)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
