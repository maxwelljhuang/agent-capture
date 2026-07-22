"""Compliance metadata (Section 4.3).

These fields are attached to every span at construction time. The reporting
layer will need them months or years later and cannot reconstruct them after
the fact — capture-at-creation is the only correct posture.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class RegulatoryRegime(StrEnum):
    """Regulatory regimes recognized by the v1 schema.

    The list is intentionally finance-first. Add new entries as verticals
    expand; never repurpose existing values. Reporting downstream keys off
    these strings.
    """

    ECOA = "ECOA"  # Equal Credit Opportunity Act
    FCRA = "FCRA"  # Fair Credit Reporting Act
    SR_11_7 = "SR_11-7"  # Federal Reserve model risk management guidance
    UDAAP = "UDAAP"  # Unfair, Deceptive, or Abusive Acts or Practices
    GLBA = "GLBA"  # Gramm-Leach-Bliley Act
    BSA_AML = "BSA_AML"  # Bank Secrecy Act / Anti-Money Laundering
    HIPAA = "HIPAA"  # Health Insurance Portability and Accountability Act
    GDPR = "GDPR"
    CCPA = "CCPA"


class DataClassification(StrEnum):
    """Sensitivity classification of the payload.

    Drives default redaction posture if no per-field policy applies.
    """

    PUBLIC = "public"
    INTERNAL = "internal"
    PII = "PII"
    PCI = "PCI"
    MNPI = "MNPI"  # Material Non-Public Information
    PHI = "PHI"  # Protected Health Information


class RetentionClass(StrEnum):
    """How long this span must be retained and under what rules.

    Concrete retention windows are policy-defined in the ledger layer; this
    enum is the *classification* that policy keys off.
    """

    STANDARD = "standard"
    EXTENDED = "extended"  # e.g., 7y for lending decisions under ECOA
    LITIGATION_HOLD = "litigation_hold"
    TRANSIENT = "transient"  # short-lived intermediate; ephemeral retention


class ComplianceMetadata(BaseModel):
    """Compliance metadata attached to every span at creation time.

    Every field listed in Section 4.3 of the architecture document is
    represented here. None of these can be reconstructed after the fact —
    the prompt template version in use when the span was created, the
    policy bundle in force, the regulatory regime applicable to the
    trajectory: these must all be captured the moment the span is born.
    """

    policy_version_active: str = Field(
        ...,
        description="Identifier of the compliance policy bundle in force when the span was created.",
    )
    prompt_template_version: str | None = Field(
        default=None,
        description="Exact version of the prompt template used. Only meaningful for model_call spans.",
    )
    model_card_version: str | None = Field(
        default=None,
        description="Approved model card governing this model use. Only meaningful for model_call spans.",
    )
    tool_schema_version: str | None = Field(
        default=None,
        description="Schema version of any tool involved. Only meaningful for tool_call spans.",
    )
    agent_version: str = Field(
        ...,
        description="Version of the agent that produced this span.",
    )
    end_customer_id: str = Field(
        ...,
        description="Which regulated customer's environment this is running in.",
    )
    subject_id: str | None = Field(
        default=None,
        description="The entity the agent is acting on (loan applicant, account holder, etc.), redacted appropriately.",
    )
    regulatory_regime: list[RegulatoryRegime] = Field(
        default_factory=list,
        description="Applicable regulations for this trajectory.",
    )
    retention_class: RetentionClass = Field(
        default=RetentionClass.STANDARD,
        description="Retention classification governing this span's lifecycle.",
    )
    data_classification: DataClassification = Field(
        default=DataClassification.INTERNAL,
        description="Sensitivity classification of the payload.",
    )
