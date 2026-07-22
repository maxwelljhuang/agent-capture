"""Top-level ``ledger`` CLI."""

from __future__ import annotations

import typer

from agent_capture_ledger.cli import attest as attest_cmd
from agent_capture_ledger.cli import db as db_cmd
from agent_capture_ledger.cli import serve as serve_cmd
from agent_capture_ledger.cli import token as token_cmd
from agent_capture_ledger.cli import verify as verify_cmd
from agent_capture_ledger.worker import main as worker_cmd

app = typer.Typer(no_args_is_help=True, add_completion=False, help="agent-capture vendor-cloud ledger")
app.add_typer(db_cmd.app, name="db", help="Database init + migrations")
app.add_typer(token_cmd.app, name="token", help="API token management")
app.add_typer(attest_cmd.app, name="attest", help="Attestations")
app.add_typer(worker_cmd.app, name="worker", help="Background workers")
app.command("serve")(serve_cmd.serve)
app.command("verify")(verify_cmd.verify_trajectory)


if __name__ == "__main__":
    app()
