"""End-to-end render of the canonical loan-denial trajectory."""

from __future__ import annotations

from datetime import datetime

import pytest
from agent_capture.schema import Span

from agent_capture_reporter.render.pdf import pdf_available
from agent_capture_reporter.report import render_adverse_action
from agent_capture_reporter.trajectory import Trajectory


def test_renders_complete_notice_html(loan_denial_spans: list[Span], generated_at: datetime) -> None:
    traj = Trajectory.from_spans(loan_denial_spans)
    rendered = render_adverse_action(traj, generated_at=generated_at, with_pdf=False)
    html = rendered.html
    # Collapse template line-wrapping so substring checks aren't whitespace-sensitive.
    flat = " ".join(html.split())

    # Reg B required content is present.
    assert "Your application for credit was denied." in flat
    assert "Principal Reasons" in flat
    assert "debt obligations are too high" in flat  # high_dti mapped to consumer language
    assert "delinquent" in flat  # delinquencies mapped
    # FCRA CRA identity.
    assert "experian.consumer-disclosure.v1" in flat
    # The verbatim ECOA notice language.
    assert "Equal Credit Opportunity Act" in flat
    assert "Consumer Financial Protection Bureau" in flat
    # No gap markers in a complete notice.
    assert "[NOT CAPTURED]" not in flat


def test_manifest_is_complete_and_traceable(loan_denial_spans: list[Span], generated_at: datetime) -> None:
    traj = Trajectory.from_spans(loan_denial_spans)
    manifest = render_adverse_action(traj, generated_at=generated_at, with_pdf=False).manifest

    assert manifest.notice_type == "ecoa_adverse_action"
    assert manifest.trajectory_id == traj.trajectory_id
    assert manifest.generated_at == generated_at
    assert manifest.hash_chain_verified is True
    assert manifest.completeness_score == 1.0
    assert manifest.gaps == []

    # Every span in the trajectory is recorded with its content hash.
    assert manifest.span_content_hashes == {s.span_id: s.provenance.content_hash for s in loan_denial_spans}
    assert manifest.trajectory_root_content_hash == traj.root.provenance.content_hash

    # The notice binds to its own rendered bytes.
    import hashlib

    rendered = render_adverse_action(traj, generated_at=generated_at, with_pdf=False)
    assert manifest.html_sha256 == hashlib.sha256(rendered.html.encode("utf-8")).hexdigest()

    # Principal-reasons section traces back to the model_call span.
    reasons = next(s for s in manifest.sections if s.section_id == "principal_reasons")
    assert reasons.source_content_hashes


def test_render_is_deterministic(loan_denial_spans: list[Span], generated_at: datetime) -> None:
    traj = Trajectory.from_spans(loan_denial_spans)
    a = render_adverse_action(traj, generated_at=generated_at, with_pdf=False)
    b = render_adverse_action(traj, generated_at=generated_at, with_pdf=False)
    assert a.html == b.html
    assert a.manifest.model_dump_json() == b.manifest.model_dump_json()


@pytest.mark.pdf
def test_renders_pdf_when_weasyprint_available(loan_denial_spans: list[Span], generated_at: datetime) -> None:
    if not pdf_available():
        pytest.skip("WeasyPrint not installed")
    traj = Trajectory.from_spans(loan_denial_spans)
    rendered = render_adverse_action(traj, generated_at=generated_at, with_pdf=True)
    assert rendered.pdf is not None
    assert rendered.pdf[:5] == b"%PDF-"
    assert rendered.manifest.pdf_sha256 is not None
