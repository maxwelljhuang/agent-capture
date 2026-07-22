"""``agent-capture-report`` — render compliance notices from trajectories.

Two commands: ``adverse-action`` (ECOA, one trajectory) and ``model-inventory``
(SR 11-7, a corpus over a period). Each reads its input from either local JSONL
(the recorder's file destination) **or** directly from the vendor-cloud ledger via
``--ledger-url`` + ``--ledger-token``, and writes the document(s) + ``manifest.json``
into an output directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from agent_capture_reporter.common.corpus import ReportingPeriod, load_corpus
from agent_capture_reporter.common.ledger_source import (
    LedgerClient,
    load_corpus_from_ledger,
    load_trajectory_from_ledger,
)
from agent_capture_reporter.errors import (
    IncompleteInventoryError,
    IncompleteTrajectoryError,
    ReporterError,
)
from agent_capture_reporter.report import render_adverse_action, render_model_inventory
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceRegistry
from agent_capture_reporter.trajectory import load_trajectory

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="agent-capture compliance notice renderer (layer 3)",
)


@app.callback()
def _main() -> None:
    """agent-capture compliance notice renderer (layer 3).

    A no-op group callback so the subcommands stay named (Typer otherwise
    collapses a single-command app into a bare command).
    """


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=code)


@app.command("adverse-action")
def adverse_action(
    trajectory_path: Path | None = typer.Argument(None, help="Trajectory JSONL file (omit if using --ledger-url)."),
    out_dir: Path = typer.Option(
        Path("."), "--out", "-o", help="Directory to write notice.html / notice.pdf / manifest.json into."
    ),
    ledger_url: str | None = typer.Option(None, "--ledger-url", help="Ledger base URL (read from ledger, not a file)."),
    ledger_token: str | None = typer.Option(
        None, "--ledger-token", envvar="AGENT_CAPTURE_LEDGER_TOKEN", help="Ledger reader bearer token."
    ),
    trajectory_id: str | None = typer.Option(None, "--trajectory-id", help="Trajectory id (with --ledger-url)."),
    with_pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Also render a PDF (requires the 'pdf' extra)."),
    verify_hashes: bool = typer.Option(
        True, "--verify-hashes/--no-verify-hashes", help="Recompute and verify span content hashes on load."
    ),
) -> None:
    """Render an ECOA Adverse Action Notice from a recorded trajectory."""
    if ledger_url and trajectory_path:
        raise _fail("Provide either a trajectory path or --ledger-url, not both.")
    if ledger_url and not trajectory_id:
        raise _fail("--trajectory-id is required when reading from --ledger-url.")
    if not ledger_url and trajectory_path is None:
        raise _fail("Provide a trajectory path, or --ledger-url + --trajectory-id.")

    try:
        if ledger_url:
            with LedgerClient(ledger_url, ledger_token) as ledger:
                trajectory = load_trajectory_from_ledger(ledger, trajectory_id or "", verify_hashes=verify_hashes)
        else:
            assert trajectory_path is not None  # narrowed by the checks above
            trajectory = load_trajectory(trajectory_path, verify_hashes=verify_hashes)
        rendered = render_adverse_action(trajectory, generated_at=datetime.now(UTC), with_pdf=with_pdf)
    except IncompleteTrajectoryError as exc:
        raise _fail(f"Cannot render notice — trajectory is missing required content: {', '.join(exc.missing)}") from exc
    except ReporterError as exc:
        raise _fail(f"Reporter error: {exc}", code=2) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "notice.html"
    manifest_path = out_dir / "manifest.json"
    html_path.write_text(rendered.html, encoding="utf-8")
    manifest_path.write_text(rendered.manifest.model_dump_json(indent=2), encoding="utf-8")
    written = [html_path, manifest_path]

    if rendered.pdf is not None:
        pdf_path = out_dir / "notice.pdf"
        pdf_path.write_bytes(rendered.pdf)
        written.append(pdf_path)

    typer.secho(f"Rendered ECOA adverse action notice for trajectory {trajectory.trajectory_id}", fg=typer.colors.GREEN)
    typer.echo(f"  completeness: {rendered.manifest.completeness_score:.0%}")
    if rendered.manifest.gaps:
        typer.echo(f"  gaps: {len(rendered.manifest.gaps)} (see manifest.json)")
    for path in written:
        typer.echo(f"  wrote {path}")


@app.command("model-inventory")
def model_inventory(
    corpus_source: str | None = typer.Argument(
        None, help="Dir/glob/JSONL of trajectories (omit if using --ledger-url)."
    ),
    registry_path: Path = typer.Option(..., "--registry", "-r", help="Model governance registry (YAML or JSON)."),
    period: str = typer.Option(..., "--period", "-p", help="Reporting period 'YYYY-MM-DD:YYYY-MM-DD' (inclusive)."),
    out_dir: Path = typer.Option(
        Path("."), "--out", "-o", help="Directory for inventory.html / inventory.pdf / manifest.json."
    ),
    ledger_url: str | None = typer.Option(None, "--ledger-url", help="Ledger base URL (read from ledger, not files)."),
    ledger_token: str | None = typer.Option(
        None, "--ledger-token", envvar="AGENT_CAPTURE_LEDGER_TOKEN", help="Ledger reader bearer token."
    ),
    tenant: str | None = typer.Option(None, "--tenant", "-t", help="Scope to one end_customer_id."),
    with_pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Also render a PDF (requires the 'pdf' extra)."),
    verify_hashes: bool = typer.Option(True, "--verify-hashes/--no-verify-hashes", help="Verify span content hashes."),
) -> None:
    """Render an SR 11-7 Model Inventory from a corpus of trajectories."""
    if ledger_url and corpus_source:
        raise _fail("Provide either a corpus source or --ledger-url, not both.")
    if not ledger_url and corpus_source is None:
        raise _fail("Provide a corpus source (dir/glob/file) or --ledger-url.")

    try:
        reporting_period = ReportingPeriod.parse(period)
        if ledger_url:
            with LedgerClient(ledger_url, ledger_token) as ledger:
                corpus = load_corpus_from_ledger(ledger, reporting_period, verify_hashes=verify_hashes)
        else:
            assert corpus_source is not None  # narrowed by the checks above
            corpus = load_corpus(corpus_source, verify_hashes=verify_hashes)
        registry = ModelGovernanceRegistry.load(registry_path)
        rendered = render_model_inventory(
            corpus, registry, reporting_period, generated_at=datetime.now(UTC), tenant=tenant, with_pdf=with_pdf
        )
    except ValueError as exc:  # bad period string
        raise _fail(f"Invalid reporting period: {exc}") from exc
    except IncompleteInventoryError as exc:
        raise _fail(f"Cannot render inventory ({exc.reason_code}): {exc}") from exc
    except ReporterError as exc:
        raise _fail(f"Reporter error: {exc}", code=2) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "inventory.html"
    manifest_path = out_dir / "manifest.json"
    html_path.write_text(rendered.html, encoding="utf-8")
    manifest_path.write_text(rendered.manifest.model_dump_json(indent=2), encoding="utf-8")
    written = [html_path, manifest_path]

    if rendered.pdf is not None:
        pdf_path = out_dir / "inventory.pdf"
        pdf_path.write_bytes(rendered.pdf)
        written.append(pdf_path)

    m = rendered.manifest
    typer.secho(f"Rendered SR 11-7 model inventory ({m.total_models} model(s))", fg=typer.colors.GREEN)
    typer.echo(f"  governed: {m.governed_models}/{m.total_models}  completeness: {m.completeness_score:.0%}")
    if m.models_missing_card:
        typer.echo(f"  ungoverned (no card): {', '.join(m.models_missing_card)}")
    if m.gaps:
        typer.echo(f"  gaps: {len(m.gaps)} (see manifest.json)")
    for path in written:
        typer.echo(f"  wrote {path}")


if __name__ == "__main__":
    app()
