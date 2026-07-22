"""The ECOA :class:`NoticeManifest` — links each notice section to its source spans.

A compliance officer who receives an Adverse Action Notice must be able to ask,
of any sentence in it, "which agent spans produced this, and can I prove they
weren't tampered with?" The manifest answers both: every rendered section names
the ``provenance.content_hash`` of every span that fed it, and the shared
:class:`~agent_capture_reporter.common.manifest_base.ManifestBase` records
whether the trajectory's hash chain re-verified at render time and binds the
manifest to the exact rendered HTML/PDF bytes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_capture_reporter.common.manifest_base import (
    MANIFEST_SCHEMA_VERSION,
    ManifestBase,
    ReportGap,
)

# Backwards-compatible alias: ECOA historically called its gap type TrajectoryGap.
# It is now the shared ReportGap (``scope`` holds the section id).
TrajectoryGap = ReportGap

__all__ = ["MANIFEST_SCHEMA_VERSION", "NoticeManifest", "SectionProvenance", "TrajectoryGap"]


class SectionProvenance(BaseModel):
    """Provenance for one section of the rendered notice."""

    model_config = {"extra": "forbid"}

    section_id: str = Field(..., description="Stable key for the section, e.g. 'principal_reasons'.")
    section_title: str = Field(..., description="Human-readable section title shown to the officer.")
    status: Literal["rendered", "gap"] = Field(
        ..., description="'rendered' if content was produced from spans; 'gap' if it was missing."
    )
    source_span_ids: list[str] = Field(
        default_factory=list, description="span_ids of every span that contributed to this section."
    )
    source_content_hashes: list[str] = Field(
        default_factory=list,
        description="provenance.content_hash of each source span — the audit link back to the ledger.",
    )
    field_paths: list[str] = Field(
        default_factory=list,
        description="Dotted field paths read, e.g. 'model_call.outputs.primary_reason'.",
    )
    rendered_value_digest: str | None = Field(
        default=None,
        description="SHA-256 of the exact text placed in the notice for this section. Null for gaps.",
    )


class NoticeManifest(ManifestBase):
    """The full audit manifest for one rendered ECOA notice."""

    notice_type: Literal["ecoa_adverse_action"] = Field(
        default="ecoa_adverse_action", description="The kind of notice this manifest describes."
    )
    trajectory_id: str = Field(..., description="The trajectory the notice was rendered from.")

    # --- integrity of the inputs the notice rests on ------------------------

    trajectory_root_content_hash: str = Field(
        ..., description="content_hash of the trajectory root span — the anchor of the hash chain."
    )
    span_content_hashes: dict[str, str] = Field(
        default_factory=dict,
        description="Every span in the trajectory: span_id -> provenance.content_hash.",
    )

    # --- the section ⇄ span mapping -----------------------------------------

    sections: list[SectionProvenance] = Field(default_factory=list)
