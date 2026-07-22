"""Column registry and governance vocabulary for the Model Inventory.

Where ECOA has notice *sections*, the inventory has *columns*. Each column
declares its provenance kind — ``span_corpus`` (read from model_call spans),
``governance_registry`` (joined from the customer registry, outside the span
hash chain), or ``computed`` (aggregated from spans) — which the manifest
records per cell so auditability is never assumed to be uniform.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_capture_reporter.common.manifest_base import ProvenanceKind


@dataclass(frozen=True)
class ColumnSpec:
    """Metadata for one inventory column."""

    column_id: str
    title: str
    kind: ProvenanceKind
    field_paths: tuple[str, ...]


# The tracked columns. Order here is the manifest/document column order.
COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec(
        "identity",
        "Model Identity",
        "span_corpus",
        ("model_call.attributes.provider", "model_call.attributes.model_name", "model_call.attributes.model_version"),
    ),
    ColumnSpec("model_card", "Model Card", "span_corpus", ("model_call.compliance.model_card_version",)),
    ColumnSpec("intended_use", "Intended Use", "governance_registry", ("registry.intended_use",)),
    ColumnSpec("risk_tier", "Risk Tier", "governance_registry", ("registry.risk_tier",)),
    ColumnSpec("validation_status", "Validation Status", "governance_registry", ("registry.validation_status",)),
    ColumnSpec("limitations", "Limitations", "governance_registry", ("registry.limitations",)),
    ColumnSpec("monitoring", "Ongoing Monitoring", "governance_registry", ("registry.monitoring",)),
    ColumnSpec(
        "usage",
        "Usage",
        "computed",
        (
            "model_call.attributes.input_tokens",
            "model_call.attributes.output_tokens",
            "model_call.attributes.total_tokens",
            "model_call.start_time",
        ),
    ),
)

COLUMNS_BY_ID: dict[str, ColumnSpec] = {c.column_id: c for c in COLUMNS}
TRACKED_COLUMN_COUNT = len(COLUMNS)

# The registry-sourced governance columns, in display order.
GOVERNANCE_COLUMNS: tuple[str, ...] = (
    "intended_use",
    "risk_tier",
    "validation_status",
    "limitations",
    "monitoring",
)

# Sentinels rendered into governance cells when content is unavailable.
NO_CARD = "NO GOVERNANCE CARD"
NOT_IN_REGISTRY = "[NOT IN REGISTRY]"

# Validation-status tokens that count as "validated" for the rollup. Everything
# else (pending, expired, unknown, missing) is surfaced as unvalidated. Tokens
# are compared case-insensitively; customers may use their own vocabulary, but
# only these count toward the "validated" rollup.
_VALIDATED_TOKENS = frozenset({"validated", "approved", "in_use", "active"})


def is_validated(validation_status: str | None) -> bool:
    """Return whether a registry validation_status counts as validated."""
    if not validation_status:
        return False
    return validation_status.strip().lower() in _VALIDATED_TOKENS
