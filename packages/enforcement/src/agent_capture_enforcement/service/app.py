"""The inline verdict service (vendor cloud).

The recorder-side gate calls ``POST /verdict`` synchronously for each gated
span and gets a terminal decision (``allow`` / ``hold`` / ``block``). Rules,
versioning, and tenancy live here — never in the agent. Verdicts are logged +
counted; persisting them to a verdict store is a later concern.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.types import TypedAttributes
from fastapi import Depends, FastAPI
from pydantic import BaseModel, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_enforcement import __version__
from agent_capture_enforcement.config import get_settings
from agent_capture_enforcement.decision import decide
from agent_capture_enforcement.observability import advisory_verdicts
from agent_capture_enforcement.rules import EnforcementRuleSet, load_rules
from agent_capture_enforcement.service.auth import require_service_token
from agent_capture_enforcement.storage.engine import session_dependency
from agent_capture_enforcement.storage.repository import HoldRepo

log = structlog.get_logger(__name__)

_ATTRS_ADAPTER: TypeAdapter[TypedAttributes] = TypeAdapter(TypedAttributes)

# Cache the ruleset by (path, mtime) so the service re-reads on file change.
_ruleset_cache: tuple[str, float, EnforcementRuleSet] | None = None
_EMPTY = EnforcementRuleSet(version="none", rules=())


def current_ruleset() -> EnforcementRuleSet:
    """Load + cache the configured ruleset, or an empty one if unconfigured."""
    global _ruleset_cache
    path = get_settings().rules_path
    if path is None:
        return _EMPTY
    spath = str(path)
    mtime = os.path.getmtime(spath)
    if _ruleset_cache is not None and _ruleset_cache[0] == spath and _ruleset_cache[1] == mtime:
        return _ruleset_cache[2]
    ruleset = load_rules(spath)
    _ruleset_cache = (spath, mtime, ruleset)
    return ruleset


class VerdictRequest(BaseModel):
    """Wire shape of a gated span the recorder gate asks a verdict for."""

    span_type: SpanType
    name: str
    trajectory_id: str
    span_id: str
    parent_span_id: str | None = None
    attributes: dict[str, Any]
    compliance: dict[str, Any]
    inputs: Any | None = None


class VerdictResponse(BaseModel):
    """Terminal decision returned to the recorder gate."""

    decision: str
    reason: str = ""
    policy_name: str = "enforcement"
    policy_version: str = "none"
    rule_id: str = ""
    hold_id: str | None = None


def create_app() -> FastAPI:
    from agent_capture_enforcement.service import holds
    from agent_capture_enforcement.storage.engine import init_db

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Bootstrap the hold table in the serving loop (idempotent). Prod can
        # also run `enforcement db init` ahead of time.
        await init_db()
        # Fail-open auth is dev-only: warn loudly if either guard is unset so a
        # misconfigured production deploy doesn't silently expose /verdict and
        # the hold-resolution API to anonymous callers.
        settings = get_settings()
        open_guards = [
            name
            for name, value in (
                ("ENFORCEMENT_SERVICE_TOKEN", settings.service_token),
                ("ENFORCEMENT_REVIEWER_TOKEN", settings.reviewer_token),
            )
            if value is None
        ]
        if open_guards:
            log.warning(
                "enforcement_auth_open",
                unset=open_guards,
                detail="endpoints are UNAUTHENTICATED until these tokens are set",
            )
        if settings.reviewer_token is not None and settings.reviewer_customer is None:
            log.warning(
                "enforcement_reviewer_unscoped",
                detail=(
                    "reviewer token is not tenant-scoped; it can list/resolve ANY "
                    "tenant's holds. Set ENFORCEMENT_REVIEWER_CUSTOMER to bind it."
                ),
            )
        yield

    app = FastAPI(
        title="agent-capture enforcement",
        version=__version__,
        description="Inline verdict service for AI agent compliance enforcement (layer 5).",
        lifespan=_lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/verdict", response_model=VerdictResponse)
    async def verdict(
        req: VerdictRequest,
        _auth: None = Depends(require_service_token),
        session: AsyncSession = Depends(session_dependency),
    ) -> VerdictResponse:
        attributes = _ATTRS_ADAPTER.validate_python(req.attributes)
        compliance = ComplianceMetadata.model_validate(req.compliance)
        action_type = getattr(attributes, "action_type", None)
        result = decide(
            current_ruleset(),
            attributes=attributes,
            compliance=compliance,
            span_type=req.span_type,
            action_type=action_type,
        )
        for v in result.verdicts:
            advisory_verdicts.labels(result=v.result, span_type=req.span_type.value).inc()

        hold_id: str | None = None
        if result.decision == "hold":
            # Create the rendezvous record; the blocked recorder client polls it.
            hold = await HoldRepo(session).create(
                end_customer_id=compliance.end_customer_id,
                trajectory_id=req.trajectory_id,
                span_id=req.span_id,
                policy_name=result.policy_name,
                policy_version=result.policy_version,
                rule_id=result.rule_id,
                reason=result.reason,
                proposed_action=f"{req.name}:{action_type}" if action_type else req.name,
                hold_timeout_s=get_settings().hold_timeout_s,
            )
            await session.commit()
            hold_id = hold.hold_id

        if result.decision != "allow" or any(v.result != "pass" for v in result.verdicts):
            log.info(
                "enforcement.verdict",
                decision=result.decision,
                rule_id=result.rule_id,
                reason=result.reason,
                span_id=req.span_id,
                trajectory_id=req.trajectory_id,
                end_customer_id=compliance.end_customer_id,
                policy_version=result.policy_version,
                hold_id=hold_id,
            )
        return VerdictResponse(
            decision=result.decision,
            reason=result.reason,
            policy_version=result.policy_version,
            rule_id=result.rule_id,
            hold_id=hold_id,
        )

    app.include_router(holds.router)
    return app
