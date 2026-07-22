"""`ledger token create` argument validation (no DB — rejection paths only)."""

from __future__ import annotations

import re

from click.testing import Result
from typer.testing import CliRunner

from agent_capture_ledger.cli.token import app

runner = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _text(result: Result) -> str:
    """Combined stdout+stderr, ANSI-stripped.

    Click <8.2 mixes stderr into ``output``; >=8.2 separates it. Typer renders
    BadParameter via rich to stderr, so we read both and strip styling.
    """
    text = result.output or ""
    try:
        text += result.stderr or ""
    except ValueError:  # stderr not separately captured (older click)
        pass
    return _ANSI.sub("", text)


def test_unscoped_only_with_ingest() -> None:
    result = runner.invoke(app, ["create", "--role", "reader", "--unscoped"])
    assert result.exit_code == 2  # click usage/param error
    assert "only valid with --role ingest" in _text(result)


def test_unscoped_conflicts_with_customer() -> None:
    result = runner.invoke(app, ["create", "--role", "ingest", "--unscoped", "--customer", "acme"])
    assert result.exit_code == 2
    assert "conflicts with --customer" in _text(result)


def test_ingest_requires_customer_or_unscoped() -> None:
    result = runner.invoke(app, ["create", "--role", "ingest"])
    assert result.exit_code == 2
    assert "--customer required" in _text(result)


def test_bad_role_rejected() -> None:
    result = runner.invoke(app, ["create", "--role", "wizard"])
    assert result.exit_code == 2
    assert "role must be ingest|reader|admin" in _text(result)
