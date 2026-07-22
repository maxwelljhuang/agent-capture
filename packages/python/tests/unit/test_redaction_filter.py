"""End-to-end redaction filter tests + builder wiring."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_capture.exporter.base import SpanExporter
from agent_capture.redaction import (
    RedactionFilter,
    parse_policy,
)
from agent_capture.schema import (
    ComplianceMetadata,
    ProvenanceFields,
    Span,
    SpanType,
)
from agent_capture.schema.canonical import content_hash
from agent_capture.schema.compliance import (
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import ToolCallAttributes
from agent_capture.span.builder import SpanBuilder


def _policy_doc() -> dict:
    return {
        "version": "v1",
        "default_strategy": "full",
        "strategies": {
            "full": {"type": "full"},
            "hmac": {"type": "hmac", "key": "k"},
        },
        "field_rules": [
            {"field_name": "ssn", "strategy": "full"},
            {"field_name": "account_number", "strategy": "hmac"},
        ],
        "pattern_rules": [
            {"field_type": "ssn", "strategy": "full"},
            {"field_type": "routing_number", "strategy": "hmac"},
        ],
    }


def _compliance() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _bare_span() -> Span:
    return Span(
        span_id="a" * 16,
        parent_span_id=None,
        trajectory_id="a" * 32,
        name="t",
        type=SpanType.TOOL_CALL,
        start_time=datetime(2026, 5, 17, tzinfo=UTC),
        end_time=datetime(2026, 5, 17, tzinfo=UTC),
        attributes=ToolCallAttributes(
            tool_name="loan",
            arguments={"ssn": "111-22-3333", "account_number": "12345678"},
        ),
        inputs={"applicant": {"ssn": "555-44-3333", "notes": "ABA: 011000015"}},
        outputs={"result": "approved"},
        compliance=_compliance(),
        provenance=ProvenanceFields(content_hash="0" * 64),
    )


class _Cap(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def shutdown(self, timeout: float = 5.0) -> None:
        pass


def test_filter_redacts_named_fields() -> None:
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    out = flt.apply(_bare_span())
    assert out.attributes.arguments["ssn"] == "[REDACTED:ssn]"
    assert out.attributes.arguments["account_number"].startswith("[FP:")
    assert out.inputs["applicant"]["ssn"] == "[REDACTED:ssn]"


def test_filter_pattern_scans_free_text() -> None:
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    out = flt.apply(_bare_span())
    notes = out.inputs["applicant"]["notes"]
    assert "011000015" not in notes
    assert "[FP:" in notes  # routing → hmac per pattern_rules


def test_filter_returns_valid_span() -> None:
    """Filter output must still pass Pydantic validation."""
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    out = flt.apply(_bare_span())
    # Re-validate through Pydantic — round-trip.
    Span.model_validate(out.model_dump())


def _span_with_subject(subject_id: str) -> Span:
    s = _bare_span()
    return s.model_copy(update={"compliance": _compliance().model_copy(update={"subject_id": subject_id})})


def test_subject_id_fingerprinted_with_key(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_CAPTURE_HMAC_KEY", "test-subject-key")
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    out = flt.apply(_span_with_subject("APP-10293"))
    assert out.compliance.subject_id.startswith("[FP:")
    assert out.compliance.subject_id.endswith(":subject_id]")
    assert "APP-10293" not in out.compliance.subject_id
    # deterministic + idempotent — re-applying doesn't double-wrap
    assert flt.apply(out).compliance.subject_id == out.compliance.subject_id


def test_subject_id_full_redacted_without_key(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_CAPTURE_HMAC_KEY", raising=False)
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    out = flt.apply(_span_with_subject("APP-10293"))
    assert out.compliance.subject_id == "[REDACTED:subject_id]"


def test_builder_runs_filter_before_hash() -> None:
    """content_hash must cover post-redaction bytes."""
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))
    exp_with_filter = _Cap()
    b_with = SpanBuilder(exp_with_filter, default_compliance=_compliance(), redaction_filter=flt)
    o = b_with.open(
        name="t",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(
            tool_name="loan",
            arguments={"ssn": "111-22-3333"},
        ),
    )
    b_with.close(o, outputs={"result": "ok"})
    span_with = exp_with_filter.spans[0]
    # The arguments dict has been redacted.
    assert span_with.attributes.arguments["ssn"] == "[REDACTED:ssn]"
    # And content_hash is over the redacted form: recomputing matches.
    assert span_with.provenance.content_hash == content_hash(span_with)


def test_filter_fallback_when_internal_failure(monkeypatch) -> None:
    """A broken filter pipeline must still produce a (over-)redacted span — never the original."""
    flt = RedactionFilter(policy=parse_policy(_policy_doc()))

    # Force the schema-aware pass to raise.
    def boom(self, value):  # type: ignore[no-untyped-def]
        raise RuntimeError("induced failure")

    monkeypatch.setattr("agent_capture.redaction.schema_aware.SchemaAwareRedactor.redact", boom)
    out = flt.apply(_bare_span())
    # Sensitive value must NOT survive verbatim. The fallback over-redacts.
    flat = str(out.model_dump())
    assert "111-22-3333" not in flat
    assert "12345678" not in flat
    assert "555-44-3333" not in flat


def test_builder_skips_filter_when_none() -> None:
    exp = _Cap()
    b = SpanBuilder(exp, default_compliance=_compliance(), redaction_filter=None)
    o = b.open(
        name="t",
        type=SpanType.TOOL_CALL,
        attributes=ToolCallAttributes(tool_name="loan", arguments={"ssn": "111-22-3333"}),
    )
    b.close(o)
    # No redaction occurred — the raw value survives.
    assert exp.spans[0].attributes.arguments["ssn"] == "111-22-3333"
