"""`ledger attest` — manual attestation + verification."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="Attestation management")


@app.command("now")
def now(
    customer: str = typer.Option(..., "--customer"),
    window_hours: int = typer.Option(1, "--window-hours"),
) -> None:
    """Build a window attestation for ``--customer`` ending now."""
    from agent_capture_ledger.integrity.attestation import (
        AttestationResult,
        attestation_envelope,
        build_window,
    )

    _ = AttestationResult  # used in inner _go() annotation
    from agent_capture_ledger.integrity.signer import load_signer
    from agent_capture_ledger.storage.engine import get_session_factory

    try:
        signer = load_signer()  # KMS-backed if LEDGER_SIGNING_KMS_KEY is set
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    end = datetime.now(UTC)
    start = end - timedelta(hours=window_hours)

    async def _go() -> AttestationResult | None:
        async with get_session_factory()() as session:
            result = await build_window(
                session,
                end_customer_id=customer,
                window_start=start,
                window_end=end,
                signer=signer,
            )
            await session.commit()
        return result

    result = asyncio.run(_go())
    if result is None:
        typer.echo("no closed trajectories in window")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(attestation_envelope(result), indent=2))


@app.command("verify")
def verify(
    attestation_file: Path = typer.Argument(..., exists=True, readable=True),
    public_key: Path = typer.Option(..., "--pub", exists=True, readable=True),
) -> None:
    """Verify a signed attestation file against a public key."""
    from agent_capture_ledger.integrity.attestation import signing_payload
    from agent_capture_ledger.integrity.signer import verify_signature

    env = json.loads(Path(attestation_file).read_text())
    payload = signing_payload(
        root=env["merkle_root"],
        window_end=datetime.fromisoformat(env["window_end"]),
        end_customer_id=env["end_customer_id"],
    )
    sig = bytes.fromhex(env["signature"])
    ok = verify_signature(Path(public_key).read_bytes(), payload, sig)
    if ok:
        typer.echo("ok: signature verified")
    else:
        typer.echo("FAIL: signature did not verify", err=True)
        raise typer.Exit(code=1)
