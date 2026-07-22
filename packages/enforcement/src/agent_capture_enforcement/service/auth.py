"""Bearer-token guards for the enforcement service.

Two roles: the **service token** authenticates the recorder client
(``/verdict``, resolution polling); the **reviewer token** authenticates human
reviewers (``/holds`` list + resolve). Either guard is a no-op when its token
is unset (dev/open mode).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from agent_capture_enforcement.config import get_settings


def _matches(authorization: str | None, token: str) -> bool:
    """Constant-time bearer check — never short-circuit on the first differing byte."""
    return hmac.compare_digest(authorization or "", f"Bearer {token}")


def require_service_token(authorization: str | None = Header(default=None)) -> None:
    """Require the service token if one is configured."""
    token = get_settings().service_token
    if token is None:
        return
    if not _matches(authorization, token):
        raise HTTPException(status_code=401, detail="invalid or missing service token")


def require_reviewer_token(authorization: str | None = Header(default=None)) -> str | None:
    """Require the reviewer token if one is configured.

    Returns the tenant the reviewer is bound to (``ENFORCEMENT_REVIEWER_CUSTOMER``)
    or ``None`` for an unscoped/admin reviewer. Handlers enforce the binding.
    """
    settings = get_settings()
    token = settings.reviewer_token
    if token is None:
        return settings.reviewer_customer
    if not _matches(authorization, token):
        raise HTTPException(status_code=401, detail="invalid or missing reviewer token")
    return settings.reviewer_customer
