"""Turn rule evaluations into a terminal gate decision (allow / hold / block).

This is the brain of the verdict service. ``mode`` is the rollout gate: a rule
in ``advisory`` mode records its verdict but never affects the decision (so a
rule can ship live and silent). A ``blocking`` rule whose predicate fails
escalates per its ``failure_mode``:

- ``fail_open``   → allow (record only)
- ``fail_to_human`` → hold (escalate to a human; see the hold queue)
- ``fail_closed`` → block (refuse the action)

``on_fail=warn`` marks a soft finding that never escalates, even when blocking.
The most severe decision across all firing rules wins (block > hold > allow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.types import TypedAttributes

from agent_capture_enforcement.advisory import evaluate_rules
from agent_capture_enforcement.evaluator import get_evaluator
from agent_capture_enforcement.rules import EnforcementRule, EnforcementRuleSet
from agent_capture_enforcement.verdict import EnforcementVerdict

Decision = Literal["allow", "hold", "block"]

_SEVERITY: dict[Decision, int] = {"allow": 0, "hold": 1, "block": 2}
_FAILURE_DECISION: dict[str, Decision] = {"fail_open": "allow", "fail_to_human": "hold", "fail_closed": "block"}


@dataclass(frozen=True)
class DecisionResult:
    """The verdict service's answer for one gated span."""

    decision: Decision
    policy_version: str
    reason: str = ""
    rule_id: str = ""
    policy_name: str = "enforcement"
    verdicts: tuple[EnforcementVerdict, ...] = field(default_factory=tuple)


def _decision_for(rule: EnforcementRule, failed: bool) -> Decision:
    if not failed or rule.on_fail != "fail" or rule.mode != "blocking":
        return "allow"
    return _FAILURE_DECISION[rule.failure_mode]


def decide(
    ruleset: EnforcementRuleSet,
    *,
    attributes: TypedAttributes,
    compliance: ComplianceMetadata,
    span_type: SpanType,
    action_type: str | None = None,
) -> DecisionResult:
    """Evaluate applicable rules and return the most-severe terminal decision."""
    verdicts: list[EnforcementVerdict] = []
    decision: Decision = "allow"
    reason = ""
    rule_id = ""

    for rule in ruleset.rules_for(span_type, action_type):
        outcome = get_evaluator(rule.evaluator)(attributes, compliance, rule.params)
        failed = outcome.result == "fail"
        result = rule.on_fail if failed else outcome.result
        verdicts.append(
            EnforcementVerdict(
                rule_id=rule.id,
                result=result,
                policy_version=ruleset.version,
                reason=outcome.reason,
            )
        )
        candidate = _decision_for(rule, failed)
        if _SEVERITY[candidate] > _SEVERITY[decision]:
            decision = candidate
            reason = outcome.reason
            rule_id = rule.id

    return DecisionResult(
        decision=decision,
        policy_version=ruleset.version,
        reason=reason,
        rule_id=rule_id,
        verdicts=tuple(verdicts),
    )


__all__ = ["Decision", "DecisionResult", "decide", "evaluate_rules"]
