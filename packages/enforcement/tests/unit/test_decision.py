"""Decision logic: rule evaluations -> terminal allow/hold/block."""

from __future__ import annotations

from agent_capture.schema import SpanType
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import SideEffectAttributes

from agent_capture_enforcement.decision import decide
from agent_capture_enforcement.rules import parse_rules


def _comp() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _se() -> SideEffectAttributes:
    return SideEffectAttributes(action_type="payment.wire", target_system="bank", success=True)


def _decide(rule: dict[str, object]):  # type: ignore[no-untyped-def]
    rs = parse_rules({"version": "v1", "rules": [{"id": "r", "span_type": "side_effect", **rule}]})
    return decide(rs, attributes=_se(), compliance=_comp(), span_type=SpanType.SIDE_EFFECT)


def test_advisory_rule_records_but_allows() -> None:
    res = _decide({"evaluator": "always_fail", "mode": "advisory"})
    assert res.decision == "allow"
    assert res.verdicts[0].result == "fail"  # recorded, not enforced


def test_blocking_fail_closed_blocks() -> None:
    res = _decide({"evaluator": "always_fail", "mode": "blocking", "failure_mode": "fail_closed"})
    assert res.decision == "block"
    assert res.rule_id == "r"


def test_blocking_fail_to_human_holds() -> None:
    res = _decide({"evaluator": "always_fail", "mode": "blocking", "failure_mode": "fail_to_human"})
    assert res.decision == "hold"


def test_blocking_fail_open_allows() -> None:
    res = _decide({"evaluator": "always_fail", "mode": "blocking", "failure_mode": "fail_open"})
    assert res.decision == "allow"


def test_on_fail_warn_never_escalates() -> None:
    res = _decide({"evaluator": "always_fail", "mode": "blocking", "failure_mode": "fail_closed", "on_fail": "warn"})
    assert res.decision == "allow"
    assert res.verdicts[0].result == "warn"


def test_passing_rule_allows() -> None:
    res = _decide({"evaluator": "always_pass", "mode": "blocking", "failure_mode": "fail_closed"})
    assert res.decision == "allow"


def test_most_severe_wins() -> None:
    rs = parse_rules(
        {
            "version": "v1",
            "rules": [
                {
                    "id": "hold",
                    "span_type": "side_effect",
                    "evaluator": "always_fail",
                    "mode": "blocking",
                    "failure_mode": "fail_to_human",
                },
                {
                    "id": "block",
                    "span_type": "side_effect",
                    "evaluator": "always_fail",
                    "mode": "blocking",
                    "failure_mode": "fail_closed",
                },
            ],
        }
    )
    res = decide(rs, attributes=_se(), compliance=_comp(), span_type=SpanType.SIDE_EFFECT)
    assert res.decision == "block"
    assert res.rule_id == "block"
