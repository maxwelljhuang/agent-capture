"""Orchestration: trajectory → rendered notice (HTML + PDF) + audit manifest.

This ties the pieces together: extract the view-model and provenance
(:mod:`agent_capture_reporter.ecoa.extract`), render HTML and optionally PDF
(:mod:`agent_capture_reporter.render`), and assemble the
:class:`~agent_capture_reporter.manifest.schema.NoticeManifest` that binds the
notice sections to their source spans and to the exact rendered bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod
from agent_capture_reporter.common.provenance import verify_hash_chain
from agent_capture_reporter.ecoa.extract import extract_adverse_action
from agent_capture_reporter.manifest.inventory import ModelInventoryManifest
from agent_capture_reporter.manifest.schema import NoticeManifest
from agent_capture_reporter.render.html import render, render_html
from agent_capture_reporter.render.pdf import render_pdf
from agent_capture_reporter.sr_11_7.extract import DEFAULT_SAMPLE_SIZE, extract_model_inventory
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceRegistry
from agent_capture_reporter.trajectory import Trajectory
from agent_capture_reporter.version import __version__


@dataclass
class RenderedNotice:
    """The full output of a render: the document plus its audit manifest."""

    html: str
    pdf: bytes | None
    manifest: NoticeManifest


@dataclass
class RenderedInventory:
    """The full output of an inventory render: document + aggregate manifest."""

    html: str
    pdf: bytes | None
    manifest: ModelInventoryManifest


def render_adverse_action(
    trajectory: Trajectory,
    *,
    generated_at: datetime,
    with_pdf: bool = True,
) -> RenderedNotice:
    """Render an ECOA Adverse Action Notice from a trajectory.

    Args:
        trajectory: The loaded, validated trajectory for one credit decision.
        generated_at: Timestamp stamped into the manifest. Caller-supplied so
            output is reproducible (tests pass a fixed value).
        with_pdf: When ``True``, also render a PDF via WeasyPrint. Set ``False``
            (or omit the ``pdf`` extra) to produce HTML + manifest only.

    Returns:
        A :class:`RenderedNotice` with HTML, optional PDF, and the manifest.

    Raises:
        IncompleteTrajectoryError: If a Regulation B required element is absent.
        RenderError: If PDF rendering is requested but fails.
    """
    extraction = extract_adverse_action(trajectory)

    html = render_html(extraction.model)
    html_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()

    pdf: bytes | None = None
    pdf_sha256: str | None = None
    if with_pdf:
        pdf = render_pdf(html)
        pdf_sha256 = hashlib.sha256(pdf).hexdigest()

    manifest = NoticeManifest(
        trajectory_id=trajectory.trajectory_id,
        generated_at=generated_at,
        reporter_version=__version__,
        trajectory_root_content_hash=trajectory.root.provenance.content_hash,
        span_content_hashes={s.span_id: s.provenance.content_hash for s in trajectory.spans},
        hash_chain_verified=verify_hash_chain(trajectory.spans),
        sections=extraction.sections,
        gaps=extraction.gaps,
        completeness_score=extraction.completeness_score,
        html_sha256=html_sha256,
        pdf_sha256=pdf_sha256,
    )
    return RenderedNotice(html=html, pdf=pdf, manifest=manifest)


def render_model_inventory(
    corpus: Corpus,
    registry: ModelGovernanceRegistry,
    period: ReportingPeriod,
    *,
    generated_at: datetime,
    tenant: str | None = None,
    with_pdf: bool = True,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> RenderedInventory:
    """Render an SR 11-7 Model Inventory from a corpus + governance registry.

    Args:
        corpus: The loaded trajectories covering the reporting period.
        registry: The customer-supplied model governance registry.
        period: The inclusive reporting period.
        generated_at: Timestamp stamped into the document + manifest.
        tenant: Required only to disambiguate a multi-tenant corpus.
        with_pdf: Also render a PDF via WeasyPrint when ``True``.
        sample_size: Max span citations sampled per model row in the manifest.

    Returns:
        A :class:`RenderedInventory` with HTML, optional PDF, and the manifest.

    Raises:
        IncompleteInventoryError: If no inventory can be formed (see extractor).
        RenderError: If PDF rendering is requested but fails.
    """
    extraction = extract_model_inventory(corpus, registry, period, tenant=tenant, sample_size=sample_size)
    model = extraction.model.model_copy(update={"generated_date": generated_at.date().isoformat()})

    html = render("model_inventory.html.j2", {"inv": model})
    html_sha256 = hashlib.sha256(html.encode("utf-8")).hexdigest()

    pdf: bytes | None = None
    pdf_sha256: str | None = None
    if with_pdf:
        pdf = render_pdf(html)
        pdf_sha256 = hashlib.sha256(pdf).hexdigest()

    manifest = ModelInventoryManifest(
        generated_at=generated_at,
        reporter_version=__version__,
        hash_chain_verified=verify_hash_chain(corpus.all_spans()),
        completeness_score=extraction.completeness_score,
        gaps=extraction.gaps,
        html_sha256=html_sha256,
        pdf_sha256=pdf_sha256,
        reporting_period_start=period.start,
        reporting_period_end=period.end,
        tenant=tenant,
        trajectories_scanned=len(corpus),
        entries=extraction.entries_provenance,
        total_models=model.total_models,
        governed_models=model.governed_models,
        models_missing_card=extraction.models_missing_card,
        models_missing_registry_entry=extraction.models_missing_registry_entry,
    )
    return RenderedInventory(html=html, pdf=pdf, manifest=manifest)
