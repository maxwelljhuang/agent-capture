"""Per-type attribute models (Section 4.2).

Each span has a ``type`` discriminator and a typed ``attributes`` payload
matching that type. The ``TypedAttributes`` union below is the discriminated
union that lives on :class:`agent_capture.schema.span.Span`.

The compliance-specific types — ``human_approval``, ``side_effect``,
``policy_check`` — are the architecture's distinguishing additions over
generic observability tools. Reporting downstream keys off these
discriminators; a regulator asking "did the agent actually send the denial
letter?" is asking about a ``side_effect`` span, not a ``tool_call``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class _AttributesBase(BaseModel):
    """Common base for per-type attribute models.

    Each subclass declares a ``kind`` literal that doubles as the
    discriminator for the :data:`TypedAttributes` union.
    """

    model_config = {"extra": "forbid"}


class ModelCallAttributes(_AttributesBase):
    """Attributes for an LLM invocation."""

    kind: Literal["model_call"] = "model_call"
    model_name: str
    model_version: str | None = None
    provider: str = Field(..., description="e.g. 'anthropic', 'openai', 'bedrock', 'vertex'.")
    prompt_template_id: str | None = None
    prompt_template_version: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class ToolCallAttributes(_AttributesBase):
    """Attributes for invocation of a tool the agent has access to."""

    kind: Literal["tool_call"] = "tool_call"
    tool_name: str
    tool_schema_version: str | None = None
    arguments: dict[str, Any] | None = None
    return_value: Any | None = None


class RetrievalAttributes(_AttributesBase):
    """Attributes for fetching information from a knowledge source."""

    kind: Literal["retrieval"] = "retrieval"
    source_identifier: str = Field(..., description="Vector DB name, doc store id, external API url, etc.")
    query: str | None = None
    returned_document_ids: list[str] = Field(default_factory=list)
    relevance_scores: list[float] = Field(default_factory=list)


class PlannerStepAttributes(_AttributesBase):
    """Attributes for an internal decision point in the agent's control flow."""

    kind: Literal["planner_step"] = "planner_step"
    decision_rationale: str | None = None
    options_considered: list[str] = Field(default_factory=list)
    chosen_option: str | None = None


class SubAgentInvocationAttributes(_AttributesBase):
    """Attributes for invoking another agent. The sub-agent's spans nest under this one."""

    kind: Literal["sub_agent_invocation"] = "sub_agent_invocation"
    sub_agent_identity: str
    sub_agent_version: str | None = None


class HumanApprovalAttributes(_AttributesBase):
    """Attributes for a human reviewing and approving (or rejecting) a proposed action."""

    kind: Literal["human_approval"] = "human_approval"
    approver_identity: str
    approver_role: str
    decision: Literal["approved", "rejected", "escalated"]
    decision_timestamp: str = Field(..., description="ISO 8601 UTC timestamp of the decision.")
    artifact_reviewed: str = Field(..., description="Stable identifier of the artifact reviewed (e.g. document hash).")
    signature: str | None = Field(
        default=None,
        description="Optional cryptographic signature over the decision. Format is policy-defined.",
    )


class SideEffectAttributes(_AttributesBase):
    """Attributes for an action that affected the outside world.

    Distinct from ``tool_call`` because the question is about external state
    change, not the agent's local action. A regulator asking 'did the agent
    actually send the denial letter?' is asking about this span type.
    """

    kind: Literal["side_effect"] = "side_effect"
    action_type: str = Field(..., description="e.g. 'email.send', 'payment.transfer', 'record.update'.")
    target_system: str = Field(..., description="The external system that received the effect.")
    payload_summary: str | None = Field(
        default=None,
        description="Human-readable summary of what was sent. Full payload lives in span inputs.",
    )
    idempotency_key: str | None = None
    success: bool = Field(..., description="Did the external action succeed?")


class PolicyCheckAttributes(_AttributesBase):
    """Attributes for an automated rule evaluation."""

    kind: Literal["policy_check"] = "policy_check"
    policy_name: str
    policy_version: str
    result: Literal["pass", "fail", "warn", "not_applicable"]
    rule_details: dict[str, Any] | None = None


TypedAttributes = Annotated[
    ModelCallAttributes
    | ToolCallAttributes
    | RetrievalAttributes
    | PlannerStepAttributes
    | SubAgentInvocationAttributes
    | HumanApprovalAttributes
    | SideEffectAttributes
    | PolicyCheckAttributes,
    Field(discriminator="kind"),
]
"""Discriminated union of per-type attribute payloads.

The ``kind`` field on each variant matches the parent span's ``type`` field.
The span builder enforces that match at construction time.
"""
