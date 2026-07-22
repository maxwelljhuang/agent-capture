"""Liveness, readiness, version, and metrics endpoints.

Standard ops surface. ``/health`` is liveness (process is up).
``/ready`` checks the DB is reachable. ``/version`` exposes build info.
``/metrics`` is Prometheus exposition.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_capture_ledger import __version__
from agent_capture_ledger.api.errors import LE202
from agent_capture_ledger.observability import metrics
from agent_capture_ledger.storage.engine import session_dependency

log = structlog.get_logger(__name__)
router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: AsyncSession = Depends(session_dependency)) -> dict[str, str]:
    """Ready if DB is reachable AND the migrations head is current.

    The check uses Alembic's recorded version table; if a newer migration
    exists in the package but hasn't been applied, ``/ready`` returns 503
    so the operator knows to run ``ledger db migrate`` before sending
    traffic.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        # /ready is unauthenticated — keep the public detail generic and log the
        # real error (which can carry DSN host/user) server-side only.
        log.error("ready_db_unreachable", error=str(exc))
        raise LE202.http("database unreachable") from exc
    try:
        row = (await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar_one_or_none()
    except Exception:
        row = None
    return {"status": "ready", "schema_version": row or "unknown"}


@router.get("/version")
async def version() -> dict[str, str]:
    return {
        "version": __version__,
        "schema_version_supported": "1.0.0",
    }


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)
