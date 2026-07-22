"""Shared manifest scaffolding for every report type.

Both the ECOA notice manifest and the SR 11-7 inventory manifest carry the same
integrity/artifact fields (when it was generated, by what version, whether the
span hash chain re-verified, and the SHA-256 of the rendered HTML/PDF). Those
live on :class:`ManifestBase`; each report type extends it with its own
section/row payload.

:class:`ReportGap` is the shared "this content was missing or incomplete"
record. Its ``scope`` is whatever the report type keys gaps by — a section id
for ECOA, a model key for SR 11-7. :class:`Citation` is a single span pointer
used wherever a manifest cites an individual span.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MANIFEST_SCHEMA_VERSION = "1.0.0"
"""Manifest schema version. Independent of the span schema version."""

GapSeverity = Literal["required", "expected"]
"""``required`` — for ECOA, absence blocks rendering; for SR 11-7, the deficiency
is rendered as a finding (see each renderer's gap posture).
``expected`` — content normally present but not legally required; rendered with
a marker instead of failing."""

ProvenanceKind = Literal["span_corpus", "governance_registry", "computed"]
"""How a manifest cell was sourced: from hash-chained spans, from the
(out-of-chain) governance registry, or computed/aggregated from spans."""


class ReportGap(BaseModel):
    """A piece of report content the inputs did not fully supply."""

    model_config = {"extra": "forbid"}

    scope: str = Field(..., description="What the gap is about: a section id (ECOA) or model key (SR 11-7).")
    severity: GapSeverity = Field(..., description="'required' or 'expected'.")
    reason: str = Field(..., description="Human-readable explanation of what was absent.")


class Citation(BaseModel):
    """A pointer to a single span, for manifest-level audit trails."""

    model_config = {"extra": "forbid"}

    trajectory_id: str
    span_id: str
    content_hash: str = Field(..., description="provenance.content_hash of the cited span.")


class ManifestBase(BaseModel):
    """Fields common to every report manifest."""

    model_config = {"extra": "forbid"}

    schema_version: str = Field(default=MANIFEST_SCHEMA_VERSION)
    generated_at: datetime = Field(
        ..., description="When the manifest was produced. Caller-supplied so output is reproducible."
    )
    reporter_version: str = Field(..., description="Version of agent-capture-reporter that rendered the report.")
    hash_chain_verified: bool = Field(
        ..., description="Did every recomputed span content_hash match the recorded value at render time?"
    )
    completeness_score: float = Field(..., ge=0.0, le=1.0, description="Resolved cells / total expected cells.")
    gaps: list[ReportGap] = Field(
        default_factory=list,
        description="Always present (empty when complete) so silence is never read as completeness.",
    )
    html_sha256: str = Field(..., description="SHA-256 of the rendered HTML bytes.")
    pdf_sha256: str | None = Field(
        default=None, description="SHA-256 of the rendered PDF bytes. Null if PDF was not produced."
    )
