"""Advisory evaluation + ingest-hook tests (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_capture.schema import ProvenanceFields, Span, SpanStatus, SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import (
    RetrievalAttributes,
    SideEffectAttributes,
    TypedAttributes,
)

from agent_capture_enforcement.advisory import advisory_observe, evaluate_rules, run_advisory
from agent_capture_enforcement.observability import advisory_verdicts
from agent_capture_enforcement.rules import parse_rules


def _comp() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _span(type_: SpanType, attrs: TypedAttributes) -> Span:
    now = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    return Span(
        span_id="1" * 16,
        parent_span_id=None,
        trajectory_id="a" * 32,
        name="x",
        type=type_,
        start_time=now,
        end_time=now,
        status=SpanStatus.OK,
        attributes=attrs,
        compliance=_comp(),
        provenance=ProvenanceFields(content_hash="0" * 64, parent_content_hash=None),
    )


def _mail() -> SideEffectAttributes:
    return SideEffectAttributes(action_type="payment.wire", target_system="bank.api", success=True)


def test_evaluate_rules_fail_maps_to_on_fail() -> None:
    rs = parse_rules(
        {
            "version": "v1",
            "rules": [
                {"id": "deny", "span_type": "side_effect", "evaluator": "always_fail"},
                {"id": "soft", "span_type": "side_effect", "evaluator": "always_fail", "on_fail": "warn"},
            ],
        }
    )
    verdicts = evaluate_rules(rs, attributes=_mail(), compliance=_comp(), span_type=SpanType.SIDE_EFFECT)
    by_id = {v.rule_id: v for v in verdicts}
    assert by_id["deny"].result == "fail"
    assert by_id["soft"].result == "warn"
    assert by_id["deny"].policy_version == "v1"


def test_evaluator_exception_downgraded_to_warn() -> None:
    from agent_capture_enforcement.evaluator import register_evaluator

    def _boom(_a: object, _c: object, _p: object) -> object:
        raise RuntimeError("kaboom")

    register_evaluator("test_boom", _boom)  # type: ignore[arg-type]
    rs = parse_rules({"version": "v1", "rules": [{"id": "b", "span_type": "side_effect", "evaluator": "test_boom"}]})
    verdicts = evaluate_rules(rs, attributes=_mail(), compliance=_comp(), span_type=SpanType.SIDE_EFFECT)
    assert verdicts[0].result == "warn"
    assert "kaboom" in verdicts[0].reason


def test_run_advisory_ignores_non_gated_types() -> None:
    rs = parse_rules({"version": "v1", "rules": [{"id": "r", "span_type": "side_effect", "evaluator": "always_fail"}]})
    retrieval = _span(SpanType.RETRIEVAL, RetrievalAttributes(source_identifier="db"))
    side_effect = _span(SpanType.SIDE_EFFECT, _mail())
    results = run_advisory([retrieval, side_effect], rs)
    assert len(results) == 1
    assert results[0][0].type is SpanType.SIDE_EFFECT


def test_verdict_renders_policy_check_attributes() -> None:
    rs = parse_rules({"version": "v9", "rules": [{"id": "r", "span_type": "side_effect", "evaluator": "always_fail"}]})
    verdict = evaluate_rules(rs, attributes=_mail(), compliance=_comp(), span_type=SpanType.SIDE_EFFECT)[0]
    attrs = verdict.to_policy_check_attributes()
    assert attrs.kind == "policy_check"
    assert attrs.result == "fail"
    assert attrs.policy_version == "v9"
    assert attrs.rule_details is not None
    assert attrs.rule_details["rule_id"] == "r"


def test_advisory_observe_disabled_without_path(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ENFORCEMENT_RULES_PATH", raising=False)
    assert advisory_observe([_span(SpanType.SIDE_EFFECT, _mail())]) == []


def test_advisory_observe_evaluates_and_counts(tmp_path: Path) -> None:
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "version: v1\nrules:\n  - id: deny_wire\n    span_type: side_effect\n    evaluator: always_fail\n",
        encoding="utf-8",
    )
    before = advisory_verdicts.labels(result="fail", span_type="side_effect")._value.get()
    results = advisory_observe([_span(SpanType.SIDE_EFFECT, _mail())], rules_path=str(rules_file))
    assert len(results) == 1
    assert results[0][1].result == "fail"
    after = advisory_verdicts.labels(result="fail", span_type="side_effect")._value.get()
    assert after == before + 1
