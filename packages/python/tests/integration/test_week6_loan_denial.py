"""Week 6 exit-criterion test.

Runs the canonical loan-denial example end-to-end and asserts the
resulting trajectory satisfies every assertion in
``docs/reporting-fields.md`` (via ``scripts/verify_trajectory.py``).

If this test fails, either:
  - the example regressed (Week 6 broke), or
  - the reporting contract changed (the wishlist + verifier need updating).

Either way it's a coordinated change — never edit the test to make it
pass without first verifying the trajectory really is complete.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def example_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Execute the loan-denial example in tmp_path and return the trajectory path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_CAPTURE_HMAC_KEY", "demo-key")

    # Reset the process-wide default builder so re-runs (this test fixture
    # plus the example's configure() call) get fresh state.
    from agent_capture._internal import runtime

    runtime.set_default_builder(None)

    example_dir = REPO_ROOT / "packages" / "python" / "examples" / "loan_denial"
    spec = importlib.util.spec_from_file_location("loan_denial_run", example_dir / "run.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rc = module.main()
    assert rc == 0, f"example main() returned {rc}"

    runtime.set_default_builder(None)  # clean up for other tests

    out = tmp_path / "trajectory.jsonl"
    assert out.exists(), "example did not write trajectory.jsonl in cwd"
    return out


def test_example_produces_section_4_complete_trajectory(example_run: Path) -> None:
    """Pipe the example's trajectory through verify_trajectory.py logic."""
    # Import the verifier rather than shelling out so failures are inspectable.
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        verify_trajectory = importlib.import_module("verify_trajectory")
    finally:
        sys.path.pop(0)

    spans = verify_trajectory._parse_jsonl(example_run)
    report = verify_trajectory.verify(spans)
    assert report.ok, "Reporting contract failures:\n" + "\n".join(f"  - {f}" for f in report.failures)


def test_example_contains_every_span_type(example_run: Path) -> None:
    """The Week 6 deliverable must exercise every Section 4.2 span type."""
    types = {json.loads(line)["type"] for line in example_run.read_text().splitlines() if line.strip()}
    expected = {
        "planner_step",
        "tool_call",
        "retrieval",
        "model_call",
        "sub_agent_invocation",
        "human_approval",
        "side_effect",
        "policy_check",
    }
    missing = expected - types
    assert not missing, f"missing span types: {sorted(missing)}"


def test_example_contains_no_raw_sensitive_values(example_run: Path) -> None:
    """Redaction must have run — no raw SSN / account / routing values in shipped bytes."""
    raw = example_run.read_text()
    # The example seeds SSN 123-45-6789, account_number 12345678, and routing 011000015.
    assert "123-45-6789" not in raw, "SSN leaked into shipped bytes"
    assert '"12345678"' not in raw, "account number leaked into shipped bytes"
    assert "011000015" not in raw, "ABA routing number leaked into shipped bytes"
    # And the expected redaction sentinels are present.
    assert "[REDACTED:ssn]" in raw
    assert "[FP:" in raw  # at least one HMAC fingerprint (account/routing)


def test_example_hash_chain_is_intact(example_run: Path) -> None:
    """Every non-root span's parent_content_hash equals its parent's content_hash."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        verify_trajectory = importlib.import_module("verify_trajectory")
    finally:
        sys.path.pop(0)
    spans = verify_trajectory._parse_jsonl(example_run)
    by_id = {s.span_id: s for s in spans}
    for s in spans:
        if s.parent_span_id is None:
            assert s.provenance.parent_content_hash is None
            continue
        parent = by_id[s.parent_span_id]
        assert s.provenance.parent_content_hash == parent.provenance.content_hash, f"chain break at span {s.name}"
