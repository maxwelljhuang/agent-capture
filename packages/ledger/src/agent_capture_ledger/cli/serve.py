"""`ledger serve` — start the API."""

from __future__ import annotations

import typer
import uvicorn

from agent_capture_ledger.config import get_settings


def serve(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the ledger API server."""
    settings = get_settings()
    uvicorn.run(
        "agent_capture_ledger.api.app:app",
        host=host or settings.listen_host,
        port=port or settings.listen_port,
        reload=reload,
        log_config=None,
    )
