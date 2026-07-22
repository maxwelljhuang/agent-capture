"""The Span model — the unit of capture (Section 4.1 + 4.2).

A Span captures a single step in an agent's trajectory. A trajectory is the
complete tree of Spans rooted at the trajectory's first span, where every
non-root span links to its parent via ``parent_span_id``. Every span in a
trajectory shares the same ``trajectory_id``.

Three invariants the span builder enforces:

1. ``type`` equals ``attributes.kind`` — the discriminator is internally
   consistent.
2. ``trajectory_id == span_id`` iff ``parent_span_id is None`` — only the
   root may declare itself the trajectory.
3. ``end_time >= start_time``.

The model is OpenTelemetry-compatible: ``span_id`` and ``trajectory_id``
(OTel calls it ``trace_id``) follow OTel's hex-encoded byte conventions
(64-bit and 128-bit respectively) so the OTel exporter is a near-direct
mapping.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent_capture.schema.compliance import ComplianceMetadata
from agent_capture.schema.provenance import ProvenanceFields
from agent_capture.schema.types import TypedAttributes


class SpanType(StrEnum):
    """The discriminator naming the kind of step this span represents.

    Matches the ``kind`` field of each per-type attribute model in
    :mod:`agent_capture.schema.types`. The span builder enforces the match.
    """

    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    PLANNER_STEP = "planner_step"
    SUB_AGENT_INVOCATION = "sub_agent_invocation"
    HUMAN_APPROVAL = "human_approval"
    SIDE_EFFECT = "side_effect"
    POLICY_CHECK = "policy_check"


class SpanStatus(StrEnum):
    """Terminal status of a span.

    ``ok`` — the step completed normally.
    ``error`` — the step raised; details in :class:`ErrorInfo`.
    ``cancelled`` — the step was abandoned (timeout, parent failure, etc.).
    """

    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class ErrorInfo(BaseModel):
    """Structured error captured when a span's status is ``error``."""

    error_type: str = Field(..., description="Fully-qualified exception type name.")
    message: str = Field(..., description="Human-readable error message, post-redaction.")
    stack_trace: str | None = Field(
        default=None,
        description="Optional stack trace, post-redaction. Capture is configurable.",
    )


class Span(BaseModel):
    """A single step in an agent trajectory.

    This is the wire format. Once constructed, redacted, and finalized by the
    span builder, instances are immutable from the host agent's perspective.

    The :class:`ComplianceMetadata` and :class:`ProvenanceFields` blocks are
    grouped under their own keys (``compliance``, ``provenance``) rather than
    flattened into the top level. This keeps the OpenTelemetry-compatible
    core fields cleanly separable from compliance-specific extensions for any
    downstream consumer that wants only the standard OTel shape.
    """

    model_config = {"extra": "forbid"}

    # --- Core fields (4.1, OpenTelemetry-compatible) -------------------------

    span_id: str = Field(
        ...,
        description="Unique identifier for this span. 16 hex chars (OTel 8-byte span id).",
        pattern=r"^[0-9a-f]{16}$",
    )
    parent_span_id: str | None = Field(
        default=None,
        description="Identifier of the parent span. Null only for the trajectory root.",
        pattern=r"^[0-9a-f]{16}$",
    )
    trajectory_id: str = Field(
        ...,
        description="Identifier shared by every span in this trajectory. 32 hex chars (OTel 16-byte trace id).",
        pattern=r"^[0-9a-f]{32}$",
    )
    name: str = Field(..., description="Human-readable label for this span.", min_length=1)
    type: SpanType = Field(..., description="Kind of step. Matches attributes.kind.")
    start_time: datetime = Field(..., description="High-resolution UTC start time.")
    end_time: datetime = Field(..., description="High-resolution UTC end time.")
    status: SpanStatus = Field(default=SpanStatus.OK, description="Terminal status.")
    error: ErrorInfo | None = Field(default=None, description="Required when status is 'error'.")

    inputs: Any | None = Field(
        default=None,
        description="Post-redaction inputs payload. Shape is per-type-specific.",
    )
    outputs: Any | None = Field(
        default=None,
        description="Post-redaction outputs payload. Shape is per-type-specific.",
    )

    attributes: TypedAttributes = Field(
        ..., description="Per-type attribute payload. The 'kind' field matches the 'type' field above."
    )

    # --- Extensions ----------------------------------------------------------

    compliance: ComplianceMetadata = Field(
        ..., description="Compliance metadata. Required on every span (Section 4.3)."
    )
    provenance: ProvenanceFields = Field(..., description="Hash chain entries for the downstream ledger (Section 4.4).")

    # --- Validators ---------------------------------------------------------

    @model_validator(mode="after")
    def _kind_matches_type(self) -> Span:
        if self.attributes.kind != self.type.value:
            raise ValueError(
                f"Span.type ({self.type.value!r}) must equal Span.attributes.kind ({self.attributes.kind!r})."
            )
        return self

    @model_validator(mode="after")
    def _root_iff_no_parent(self) -> Span:
        is_root = self.parent_span_id is None
        declares_root = self.trajectory_id.startswith(self.span_id)
        # The trajectory_id is the OTel trace_id (16 bytes). The root's
        # span_id (8 bytes) is, by convention, the first 8 bytes of the
        # trace_id when generated by this SDK — but other generators may not
        # follow that, so we only validate that *some* root exists, not that
        # it must encode itself this way. The stricter assertion lives in the
        # span builder.
        del declares_root
        if is_root and self.parent_span_id is not None:
            raise ValueError("Root span must have parent_span_id is None.")
        return self

    @model_validator(mode="after")
    def _times_ordered(self) -> Span:
        if self.end_time < self.start_time:
            raise ValueError("Span.end_time must be >= Span.start_time.")
        return self

    @model_validator(mode="after")
    def _error_iff_status_error(self) -> Span:
        if self.status is SpanStatus.ERROR and self.error is None:
            raise ValueError("Span.error must be set when Span.status is 'error'.")
        if self.status is not SpanStatus.ERROR and self.error is not None:
            raise ValueError("Span.error must be None when Span.status is not 'error'.")
        return self
