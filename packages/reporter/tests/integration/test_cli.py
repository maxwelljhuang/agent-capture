"""CLI end-to-end: trajectory file in → notice.html + manifest.json out."""

from __future__ import annotations

import json

from agent_capture.schema import Span, SpanType
from typer.testing import CliRunner

from agent_capture_reporter.cli.main import app

runner = CliRunner()


def _write_jsonl(spans: list[Span], path) -> None:
    path.write_text("\n".join(s.model_dump_json() for s in spans) + "\n", encoding="utf-8")


def test_cli_writes_html_and_manifest(loan_denial_spans: list[Span], tmp_path) -> None:
    traj_path = tmp_path / "decision.jsonl"
    out_dir = tmp_path / "out"
    _write_jsonl(loan_denial_spans, traj_path)

    result = runner.invoke(app, ["adverse-action", str(traj_path), "-o", str(out_dir), "--no-pdf"])
    assert result.exit_code == 0, result.output

    html = (out_dir / "notice.html").read_text(encoding="utf-8")
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert "Your application for credit was denied." in html
    assert manifest["completeness_score"] == 1.0
    # Manifest binds to the written HTML bytes.
    import hashlib

    assert manifest["html_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert not (out_dir / "notice.pdf").exists()


def test_cli_fails_loudly_on_incomplete_trajectory(loan_denial_spans: list[Span], tmp_path) -> None:
    spans = [s for s in loan_denial_spans if s.type is not SpanType.MODEL_CALL]
    traj_path = tmp_path / "decision.jsonl"
    out_dir = tmp_path / "out"
    _write_jsonl(spans, traj_path)

    result = runner.invoke(app, ["adverse-action", str(traj_path), "-o", str(out_dir), "--no-pdf"])
    assert result.exit_code == 1
    assert "missing required content" in result.output
    assert not (out_dir / "notice.html").exists()
