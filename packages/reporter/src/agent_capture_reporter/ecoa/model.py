"""The view-model the Adverse Action Notice template binds to.

The template never reaches into raw spans — it renders this fully-resolved,
typed model. That keeps span→content logic in one place
(:mod:`agent_capture_reporter.ecoa.extract`) and makes the rendered notice
deterministic and unit-testable without touching Jinja.

Optional sub-models are ``None`` when the trajectory did not supply them; the
template renders a ``[NOT CAPTURED]`` marker in their place (the "expected gap"
half of the hybrid posture). The legally-required fields — :attr:`decision`,
:attr:`action_statement`, :attr:`principal_reasons` — are never ``None``;
absence raises before a model is ever built.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PrincipalReason(BaseModel):
    """One principal reason for the adverse action (Reg B §1002.9(b)(2))."""

    model_config = {"extra": "forbid"}

    code: str = Field(..., description="The model's reason code, e.g. 'high_dti'.")
    text: str = Field(..., description="Consumer-facing reason language.")
    known: bool = Field(..., description="Was the code resolved via the reason taxonomy (vs. rendered verbatim)?")


class CreditReportingAgency(BaseModel):
    """A consumer reporting agency consulted in the decision (FCRA §615)."""

    model_config = {"extra": "forbid"}

    source_identifier: str = Field(..., description="The retrieval source, e.g. 'experian.consumer-disclosure.v1'.")


class HumanReview(BaseModel):
    """A human approval/review step, if one occurred."""

    model_config = {"extra": "forbid"}

    approver_identity: str
    approver_role: str
    decision: str
    decision_timestamp: str


class NoticeDelivery(BaseModel):
    """Evidence that the notice was (or was attempted to be) sent."""

    model_config = {"extra": "forbid"}

    action_type: str
    target_system: str
    success: bool
    idempotency_key: str | None = None


class ProtectedClassCheck(BaseModel):
    """An automated ECOA protected-class policy check."""

    model_config = {"extra": "forbid"}

    policy_name: str
    policy_version: str
    result: str


class ModelGovernance(BaseModel):
    """Model identity/governance footer (also feeds SR 11-7, out of v1 scope)."""

    # protected_namespaces=() — these fields intentionally start with "model_".
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    model_name: str
    model_version: str | None = None
    provider: str
    model_card_version: str | None = None
    prompt_template_id: str | None = None
    prompt_template_version: str | None = None


class AdverseActionModel(BaseModel):
    """The complete, resolved content of one Adverse Action Notice."""

    # protected_namespaces=() — the model_governance field starts with "model_".
    model_config = {"extra": "forbid", "protected_namespaces": ()}

    # --- creditor / header --------------------------------------------------
    creditor_id: str = Field(..., description="The regulated customer (compliance.end_customer_id).")
    agent_version: str
    date_of_action: str = Field(..., description="Date of the adverse action (YYYY-MM-DD, UTC).")
    applicant_reference: str | None = Field(
        default=None, description="Fingerprinted applicant reference (compliance.subject_id)."
    )

    # --- legally-required content (never None) ------------------------------
    decision: str = Field(..., description="The raw decision outcome, e.g. 'deny'.")
    action_statement: str = Field(..., description="Consumer-facing statement of the action taken.")
    decision_rationale: str | None = Field(default=None, description="Internal rationale for the decision.")
    principal_reasons: list[PrincipalReason] = Field(..., min_length=1)

    # --- expected content (None / empty => rendered as a gap) ---------------
    credit_reporting_agencies: list[CreditReportingAgency] = Field(default_factory=list)
    human_review: HumanReview | None = None
    notice_delivery: NoticeDelivery | None = None
    protected_class_check: ProtectedClassCheck | None = None
    model_governance: ModelGovernance | None = None
