"""The model governance registry — the second input SR 11-7 needs.

The corpus proves *which* model card governed each model use (hash-chained via
``compliance.model_card_version``). It does **not** carry the card's contents —
intended use, risk tier, validation status, limitations, monitoring — because
the recorder deliberately captures the pointer, not the contents (see
``docs/reporting-fields.md`` §"What the recorder deliberately does not
capture"). Those live in the bank's model-risk-management system.

This module loads a **customer-owned** registry file (YAML or JSON) and joins it
to observed usage. Like the redaction policy and the ECOA reason taxonomy, the
registry is the customer's, not the vendor's. Governance cells sourced here are
explicitly *outside* the span hash chain — the manifest labels them as such.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent_capture_reporter.errors import TrajectoryLoadError


class ModelGovernanceEntry(BaseModel):
    """One model card's governance metadata, as supplied by the customer."""

    model_config = {"extra": "forbid", "protected_namespaces": ()}

    # Match keys — at least one of (model_card_version) or the identity tuple.
    model_card_version: str | None = None
    provider: str | None = None
    model_name: str | None = None
    model_version: str | None = Field(
        default=None, description="If null, the entry matches any version of (provider, model_name)."
    )

    # Governance content (any may be omitted → rendered as a per-cell gap).
    intended_use: str | None = None
    risk_tier: str | None = Field(default=None, description="Customer vocabulary, e.g. 'tier_1'.")
    validation_status: str | None = Field(default=None, description="Customer vocabulary, e.g. 'validated'.")
    last_validated: str | None = None
    valid_until: str | None = None
    limitations: str | None = None
    monitoring: str | None = Field(default=None, description="Free-text monitoring summary (owner/cadence/status).")

    @property
    def entry_ref(self) -> str:
        """A stable reference for the manifest's registry citation."""
        if self.model_card_version:
            return f"card:{self.model_card_version}"
        return f"identity:{self.provider}|{self.model_name}|{self.model_version}"


class ModelGovernanceRegistry(BaseModel):
    """A loaded registry plus the source path it came from (for manifest refs)."""

    model_config = {"extra": "forbid"}

    source: str = Field(..., description="Path/identifier of the registry, recorded in the manifest.")
    entries: list[ModelGovernanceEntry] = Field(default_factory=list)

    def match(
        self,
        *,
        provider: str,
        model_name: str,
        model_version: str | None,
        model_card_version: str | None,
    ) -> ModelGovernanceEntry | None:
        """Find the governance entry for a model, card-version first then identity.

        Precedence (per the locked join decision):
        1. Exact ``model_card_version`` match (the most specific governance link).
        2. Identity tuple ``(provider, model_name, model_version)``, where an
           entry with ``model_version is None`` matches any version.
        3. No match → ``None`` (caller records a gap).
        """
        if model_card_version:
            for e in self.entries:
                if e.model_card_version and e.model_card_version == model_card_version:
                    return e
        for e in self.entries:
            if (
                e.provider == provider
                and e.model_name == model_name
                and (e.model_version is None or e.model_version == model_version)
            ):
                return e
        return None

    @classmethod
    def load(cls, path: Path | str) -> ModelGovernanceRegistry:
        """Load a registry from a YAML (``.yaml``/``.yml``) or JSON (``.json``) file.

        Accepts either a top-level list of entries or a mapping with a ``models``
        key holding the list.

        Raises:
            TrajectoryLoadError: If the file is missing or unparseable.
        """
        p = Path(path)
        if not p.exists():
            raise TrajectoryLoadError(f"governance registry file does not exist: {p}")
        text = p.read_text(encoding="utf-8")
        try:
            data: Any = yaml.safe_load(text) if p.suffix in (".yaml", ".yml") else json.loads(text)
        except Exception as exc:  # wrap parse errors into our error type
            raise TrajectoryLoadError(f"could not parse registry {p}: {exc}") from exc

        raw_entries = data.get("models", []) if isinstance(data, dict) else data
        if not isinstance(raw_entries, list):
            raise TrajectoryLoadError(f"registry {p} must be a list of entries or a mapping with a 'models' list")
        try:
            entries = [ModelGovernanceEntry.model_validate(e) for e in raw_entries]
        except Exception as exc:  # wrap validation errors
            raise TrajectoryLoadError(f"invalid entry in registry {p}: {exc}") from exc
        return cls(source=str(p), entries=entries)
