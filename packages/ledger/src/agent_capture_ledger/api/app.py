"""FastAPI app factory.

The ledger is one process: API server + (separately invoked) workers.
``create_app()`` wires up routes, error handlers, and structured logging.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_capture_ledger import __version__
from agent_capture_ledger.api.routes import (
    access_log,
    attestations,
    health,
    holds,
    ingest,
    spans,
    stats,
    trajectories,
    verify,
)
from agent_capture_ledger.config import get_settings
from agent_capture_ledger.observability.logging import configure_logging

log = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log.info("ledger.startup", version=__version__, host=settings.listen_host, port=settings.listen_port)
    yield
    log.info("ledger.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="agent-capture ledger",
        version=__version__,
        description="Vendor-cloud ledger for AI agent compliance trajectories.",
        lifespan=_lifespan,
    )
    app.include_router(ingest.router)
    app.include_router(health.router)
    app.include_router(trajectories.router)
    app.include_router(stats.router)
    app.include_router(spans.router)
    app.include_router(verify.router)
    app.include_router(holds.router)
    app.include_router(attestations.router)
    app.include_router(access_log.router)

    @app.exception_handler(HTTPException)
    async def _problem_handler(_: Request, exc: HTTPException) -> JSONResponse:
        # If detail already looks like a ProblemDetails, pass through with
        # the correct media type; otherwise wrap generically.
        if isinstance(exc.detail, dict) and "type" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail, media_type="application/problem+json")
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return app


app = create_app()
