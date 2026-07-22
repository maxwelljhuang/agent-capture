"""Access logging — every read is itself audited.

This is the recursive integrity property: reads of trajectories are
recorded, and so are reads of the access log itself. An auditor with the
admin role can browse who read what, but their browse is in turn logged.

Usage:

    access = AccessLogger(session, request, token)
    await access.log("read.trajectory", target_kind="trajectory", target_id=tid)
"""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger.api.auth import Token
from agent_capture_ledger.storage import models


def _maybe_ip(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return None


@dataclass
class AccessLogger:
    session: AsyncSession
    request: Request
    token: Token

    async def log(
        self,
        action: str,
        *,
        target_kind: str,
        target_id: str,
    ) -> None:
        self.session.add(
            models.AccessLog(
                access_id=uuid.uuid4(),
                actor_token_id=self.token.token_id,
                actor_role=self.token.role,
                end_customer_id=self.token.end_customer_id or "<admin>",
                action=action,
                target_kind=target_kind,
                target_id=target_id,
                request_id=self.request.headers.get("x-request-id"),
                ip=_maybe_ip(self.request.client.host) if self.request.client else None,
                user_agent=self.request.headers.get("user-agent"),
            )
        )
