"""Bearer token authentication.

FastAPI dependency that resolves the ``Authorization: Bearer ...`` header
into a :class:`Token`. Routes use ``Depends(require_role("ingest"))`` etc.
to gate access.

The recorder's wire format is ``Authorization: Bearer <token>`` where the
token is the plaintext secret returned at creation. We split that into the
public token_id and the secret using a configurable delimiter and verify
against the Argon2 hash. The delimiter convention is ``<token_id>.<secret>``
— the same shape the create endpoint emits.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.errors import LE101, LE102, LE103, LE104
from agent_capture_ledger.storage.engine import session_dependency
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import verify_secret


@dataclass(frozen=True)
class Token:
    token_id: str
    role: str
    end_customer_id: str | None  # nullable for admin


def _split_bearer(value: str) -> tuple[str, str] | None:
    """Split ``<token_id>.<secret>``. Return None if malformed."""
    if "." not in value:
        return None
    token_id, secret = value.split(".", 1)
    if not token_id or not secret:
        return None
    return token_id, secret


async def get_current_token(
    request: Request,
    session: AsyncSession = Depends(session_dependency),
) -> Token:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        raise LE101.http("missing authorization header")
    raw = header.split(" ", 1)[1].strip()
    parts = _split_bearer(raw)
    if parts is None:
        raise LE102.http("malformed bearer token")
    token_id, secret = parts

    repo = TokenRepo(session)
    found = await repo.lookup(token_id)
    if found is None:
        raise LE102.http("unknown token")
    record, stored_hash = found
    if record.revoked:
        raise LE102.http("token revoked")
    if record.expires_at is not None and record.expires_at <= datetime.now(UTC):
        raise LE102.http("token expired")
    if not verify_secret(secret, stored_hash):
        raise LE102.http("token verification failed")

    return Token(token_id=record.token_id, role=record.role, end_customer_id=record.end_customer_id)


def require_role(*allowed: str) -> Callable[[Token], Awaitable[Token]]:
    """Dependency factory: enforce one of ``allowed`` roles."""

    async def _dep(token: Token = Depends(get_current_token)) -> Token:
        if token.role not in allowed:
            raise LE103.http(f"role {token.role!r} not in {list(allowed)}")
        return token

    return _dep


def require_customer_scope(token: Token, end_customer_id: str) -> None:
    """Raise LE104 if a non-admin token tries to act on a different customer."""
    if token.role == "admin":
        return
    if token.end_customer_id != end_customer_id:
        raise LE104.http("tenant scope violation")


def effective_scope(token: Token, requested: str | None) -> str | None:
    """Resolve the customer filter for a list/aggregate read.

    A non-admin token is always pinned to its own ``end_customer_id`` (the
    ``requested`` param is ignored). An ``admin`` token may filter to
    ``requested`` (or ``None`` = all tenants). This is the P6 admin filter.
    """
    if token.role == "admin":
        return requested
    return token.end_customer_id
