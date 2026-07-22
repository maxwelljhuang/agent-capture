"""`ledger token` — create/list/revoke API tokens."""

from __future__ import annotations

import asyncio

import typer

from agent_capture_ledger.storage.engine import get_session_factory
from agent_capture_ledger.storage.repository import TokenRepo
from agent_capture_ledger.tokens.hashing import (
    generate_secret,
    generate_token_id,
    hash_secret,
)

app = typer.Typer(no_args_is_help=True, help="API token management")


@app.command("create")
def create(
    role: str = typer.Option(..., "--role", help="ingest | reader | admin"),
    customer: str | None = typer.Option(None, "--customer", help="end_customer_id; required for ingest/reader"),
    unscoped: bool = typer.Option(
        False,
        "--unscoped",
        help="Mint an UNSCOPED ingest token (no tenant): it may ingest spans for ANY end_customer_id. "
        "Trusted first-party use only (a multi-tenant SaaS process). Rotate carefully.",
    ),
    label: str | None = typer.Option(None, "--label"),
    created_by: str | None = typer.Option(None, "--by", help="who's creating this"),
) -> None:
    """Create a token. PLAINTEXT IS PRINTED EXACTLY ONCE — copy it now."""
    if role not in ("ingest", "reader", "admin"):
        raise typer.BadParameter("role must be ingest|reader|admin")
    if unscoped:
        # Explicit opt-in to a tenant-wide ingest token (#63). Only for ingest;
        # cannot also pin a customer.
        if role != "ingest":
            raise typer.BadParameter("--unscoped is only valid with --role ingest")
        if customer:
            raise typer.BadParameter("--unscoped conflicts with --customer (an unscoped token has no tenant)")
    elif role != "admin" and not customer:
        raise typer.BadParameter("--customer required for ingest/reader tokens (or --unscoped for ingest)")

    token_id = generate_token_id()
    secret = generate_secret()
    token_hash = hash_secret(secret)
    bearer = f"{token_id}.{secret}"

    async def _go() -> None:
        async with get_session_factory()() as session:
            repo = TokenRepo(session)
            await repo.create(
                token_id=token_id,
                token_hash=token_hash,
                role=role,
                end_customer_id=customer,
                label=label,
                created_by=created_by,
            )
            await session.commit()

    asyncio.run(_go())

    typer.echo("ok: token created")
    typer.echo(f"  token_id : {token_id}")
    typer.echo(f"  role     : {role}")
    typer.echo(f"  customer : {customer or '<unscoped>'}")
    typer.echo("")
    typer.echo("Bearer token (shown ONCE — paste this into Authorization: Bearer ...):")
    typer.echo(f"  {bearer}")


@app.command("list")
def list_tokens() -> None:
    async def _go() -> None:
        async with get_session_factory()() as session:
            tokens = await TokenRepo(session).list_all()
            for t in tokens:
                state = "REVOKED" if t.revoked else "active"
                typer.echo(f"{t.token_id}\t{t.role}\t{t.end_customer_id or '-'}\t{state}")

    asyncio.run(_go())


@app.command("revoke")
def revoke(token_id: str = typer.Argument(...)) -> None:
    async def _go() -> bool:
        async with get_session_factory()() as session:
            ok = await TokenRepo(session).revoke(token_id)
            await session.commit()
            return ok

    if asyncio.run(_go()):
        typer.echo(f"ok: {token_id} revoked")
    else:
        typer.echo(f"warn: {token_id} not found or already revoked", err=True)
        raise typer.Exit(code=1)
