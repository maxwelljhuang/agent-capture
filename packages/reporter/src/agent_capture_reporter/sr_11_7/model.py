"""View-models the Model Inventory template binds to.

As with ECOA, the template renders these fully-resolved, typed objects rather
than reaching into spans or the registry. Governance values may carry sentinel
strings (``NO GOVERNANCE CARD`` / ``[NOT IN REGISTRY]``) so the inventory
*renders* a deficiency rather than hiding it — the inversion of ECOA's posture.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UsageMetrics(BaseModel):
    """Aggregate, span-derived usage for one model over the reporting period."""

    model_config = {"extra": "forbid", "protected_namespaces": ()}

    decision_count: int = Field(..., description="Number of in-period model_call spans for this model.")
    trajectory_count: int = Field(..., description="Distinct trajectories the model appeared in.")
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tokens_partial: bool = Field(default=False, description="True if some spans lacked token counts.")
    first_seen: str = Field(..., description="Earliest in-period model_call start_time (ISO date).")
    last_seen: str = Field(..., description="Latest in-period model_call start_time (ISO date).")
    prompt_versions: list[str] = Field(default_factory=list)
    model_card_versions: list[str] = Field(default_factory=list, description="Distinct cards observed in usage.")
    regimes: list[str] = Field(default_factory=list)


class GovernanceInfo(BaseModel):
    """Registry-sourced governance posture for one model (or its absence)."""

    model_config = {"extra": "forbid", "protected_namespaces": ()}

    intended_use: str | None = None
    risk_tier: str | None = None
    validation_status: str | None = Field(
        default=None, description="Display value; may be a NO GOVERNANCE CARD / [NOT IN REGISTRY] sentinel."
    )
    last_validated: str | None = None
    valid_until: str | None = None
    limitations: str | None = None
    monitoring: str | None = None
    source: str = Field(..., description="'registry' | 'missing_card' | 'no_registry_entry'.")


class ModelEntry(BaseModel):
    """One inventory row: a distinct (provider, model_name, model_version)."""

    model_config = {"extra": "forbid", "protected_namespaces": ()}

    provider: str
    model_name: str
    model_version: str | None
    model_key: str = Field(..., description="'provider|model_name|model_version'.")
    usage: UsageMetrics
    governance: GovernanceInfo
    governed: bool = Field(..., description="Has a model card AND a matching registry entry.")


class ModelInventoryModel(BaseModel):
    """The complete, resolved content of one SR 11-7 Model Inventory."""

    model_config = {"extra": "forbid", "protected_namespaces": ()}

    tenant: str | None
    period_start: str
    period_end: str
    generated_date: str
    entries: list[ModelEntry]
    total_models: int
    governed_models: int
    ungoverned_models: list[str] = Field(default_factory=list, description="model_keys with no governance card.")
    unvalidated_models: list[str] = Field(
        default_factory=list, description="model_keys whose validation_status is not 'validated'."
    )
    trajectories_scanned: int
