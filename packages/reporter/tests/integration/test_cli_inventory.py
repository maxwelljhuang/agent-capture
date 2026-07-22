"""CLI end-to-end for the model-inventory command."""

from __future__ import annotations

import hashlib
import json

import yaml
from agent_capture.schema import Span
from typer.testing import CliRunner

from agent_capture_reporter.cli.main import app

runner = CliRunner()


def _write_corpus(trajectories: list[list[Span]], directory) -> None:
    for i, spans in enumerate(trajectories):
        (directory / f"traj-{i}.jsonl").write_text(
            "\n".join(s.model_dump_json() for s in spans) + "\n", encoding="utf-8"
        )


def test_cli_writes_inventory_and_manifest(inventory_corpus, governance_registry_dict, tmp_path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    _write_corpus(inventory_corpus, corpus_dir)
    registry_path = tmp_path / "reg.yaml"
    registry_path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(
        app,
        [
            "model-inventory",
            str(corpus_dir),
            "--registry",
            str(registry_path),
            "--period",
            "2026-01-01:2026-03-31",
            "-o",
            str(out_dir),
            "--no-pdf",
        ],
    )
    assert result.exit_code == 0, result.output

    html = (out_dir / "inventory.html").read_text(encoding="utf-8")
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "Model Inventory" in html
    assert manifest["total_models"] == 3
    assert manifest["governed_models"] == 1
    assert manifest["html_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
    assert not (out_dir / "inventory.pdf").exists()


def test_cli_empty_period_fails(inventory_corpus, governance_registry_dict, tmp_path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    _write_corpus(inventory_corpus, corpus_dir)
    registry_path = tmp_path / "reg.yaml"
    registry_path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "model-inventory",
            str(corpus_dir),
            "--registry",
            str(registry_path),
            "--period",
            "2020-01-01:2020-12-31",
            "-o",
            str(tmp_path / "out"),
            "--no-pdf",
        ],
    )
    assert result.exit_code == 1
    assert "no_model_usage" in result.output


def test_cli_ambiguous_tenant_fails(multi_tenant_corpus, governance_registry_dict, tmp_path) -> None:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    _write_corpus(multi_tenant_corpus, corpus_dir)
    registry_path = tmp_path / "reg.yaml"
    registry_path.write_text(yaml.safe_dump(governance_registry_dict), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "model-inventory",
            str(corpus_dir),
            "--registry",
            str(registry_path),
            "--period",
            "2026-01-01:2026-03-31",
            "-o",
            str(tmp_path / "out"),
            "--no-pdf",
        ],
    )
    assert result.exit_code == 1
    assert "ambiguous_tenant" in result.output
