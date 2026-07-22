"""The SR 11-7 :class:`ModelInventoryManifest` — aggregate auditability.

One inventory row can derive from hundreds of trajectories, so per-row
provenance cannot inline every span hash the way ECOA's per-section provenance
does. Instead each row carries:

* ``contributing_trajectory_ids`` — the full (cheap) list, the navigable trail;
* ``evidence_digest`` — a **recomputable set commitment**: SHA-256 over the
  sorted unique ``model_call`` ``content_hash``es behind the row. An examiner
  pulls those trajectories from the ledger, collects the model's model_call
  hashes, sorts, hashes, and compares — the aggregate analog of ECOA citing one
  ``content_hash``;
* ``sample_citations`` — a bounded sample for spot-checks, with
  ``citations_truncated`` so a cap is never silent.

Each cell's ``ColumnProvenance.provenance_kind`` records whether it came from the
span corpus (hash-chained) or the governance registry (**outside** the hash
chain) — auditability is explicitly non-uniform for an aggregate report.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agent_capture_reporter.common.manifest_base import Citation, ManifestBase, ProvenanceKind

__all__ = ["ColumnProvenance", "ModelEntryProvenance", "ModelInventoryManifest"]


class ColumnProvenance(BaseModel):
    """Provenance for one cell (column) of one inventory row."""

    model_config = {"extra": "forbid"}

    column_id: str = Field(..., description="e.g. 'intended_use', 'usage', 'identity'.")
    status: Literal["rendered", "gap"]
    provenance_kind: ProvenanceKind = Field(..., description="'span_corpus' | 'governance_registry' | 'computed'.")
    field_paths: list[str] = Field(default_factory=list)
    registry_ref: str | None = Field(
        default=None, description="Registry source + entry ref when provenance_kind is governance_registry."
    )


class ModelEntryProvenance(BaseModel):
    """Aggregate provenance for one inventory row (one model)."""

    model_config = {"extra": "forbid"}

    model_key: str = Field(..., description="'provider|model_name|model_version'.")
    contributing_trajectory_ids: list[str] = Field(
        default_factory=list, description="Full list of trajectories whose model_calls fed this row."
    )
    contributing_span_count: int = Field(..., description="Number of model_call spans behind this row.")
    evidence_digest: str = Field(
        ..., description="SHA-256 over the sorted unique model_call content_hashes — a recomputable set commitment."
    )
    sample_citations: list[Citation] = Field(default_factory=list)
    sample_size: int = Field(..., description="How many citations were sampled.")
    citations_truncated: bool = Field(..., description="True if the sample is smaller than the contributing set.")
    columns: list[ColumnProvenance] = Field(default_factory=list)


class ModelInventoryManifest(ManifestBase):
    """The full audit manifest for one rendered SR 11-7 Model Inventory."""

    notice_type: Literal["sr_11_7_model_inventory"] = Field(default="sr_11_7_model_inventory")
    reporting_period_start: datetime
    reporting_period_end: datetime
    tenant: str | None = None
    trajectories_scanned: int

    entries: list[ModelEntryProvenance] = Field(default_factory=list)
    total_models: int
    governed_models: int = Field(..., description="Models with a card AND a matching registry entry.")
    models_missing_card: list[str] = Field(default_factory=list)
    models_missing_registry_entry: list[str] = Field(default_factory=list)
