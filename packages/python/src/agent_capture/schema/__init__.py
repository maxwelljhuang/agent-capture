"""Span schema — the source of truth for the entire capture engine.

The Pydantic models in this package are canonical. The committed
``schemas/span.schema.json`` is generated from them via
``scripts/generate_schema.py``. The committed TypeScript types under
``packages/typescript/src/schema/`` are generated from that JSON Schema.

See ``docs/architecture.md`` §4 for the field-by-field rationale.
"""

from agent_capture.schema.compliance import ComplianceMetadata, RegulatoryRegime
from agent_capture.schema.provenance import SCHEMA_VERSION, ProvenanceFields
from agent_capture.schema.span import ErrorInfo, Span, SpanStatus, SpanType
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
    SubAgentInvocationAttributes,
    ToolCallAttributes,
    TypedAttributes,
)

__all__ = [
    "SCHEMA_VERSION",
    "ComplianceMetadata",
    "ErrorInfo",
    "HumanApprovalAttributes",
    "ModelCallAttributes",
    "PlannerStepAttributes",
    "PolicyCheckAttributes",
    "ProvenanceFields",
    "RegulatoryRegime",
    "RetrievalAttributes",
    "SideEffectAttributes",
    "Span",
    "SpanStatus",
    "SpanType",
    "SubAgentInvocationAttributes",
    "ToolCallAttributes",
    "TypedAttributes",
]
