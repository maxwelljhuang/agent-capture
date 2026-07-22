"""Advisory (non-blocking) evaluation — runs at the ledger ingest boundary.

By the time a span reaches the ledger the action has already happened, so this
path can only *observe*: it produces ``policy_check`` verdicts and alerts, and
never blocks (blocking happens inline on the recorder side — see the verdict
service). Only the two gated span types are evaluated; everything else is
ignored here.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import structlog
from agent_capture.enforcement import GATED_TYPES
from agent_capture.schema import ComplianceMetadata, Span, SpanType
from agent_capture.schema.types import TypedAttributes

from agent_capture_enforcement.evaluator import EvalOutcome, get_evaluator
from agent_capture_enforcement.observability import advisory_verdicts
from agent_capture_enforcement.rules import EnforcementRuleSet, load_rules
from agent_capture_enforcement.verdict import EnforcementVerdict

log = structlog.get_logger(__name__)


def evaluate_rules(
    ruleset: EnforcementRuleSet,
    *,
    attributes: TypedAttributes,
    compliance: ComplianceMetadata,
    span_type: SpanType,
    action_type: str | None = None,
) -> list[EnforcementVerdict]:
    """Evaluate every applicable rule against one span's attributes.

    An evaluator that raises is itself downgraded to a ``warn`` verdict — a
    buggy evaluator must not silently pass a rule, but neither should it crash
    the engine. A predicate ``fail`` is recorded as the rule's ``on_fail``
    result (``fail`` or a softer ``warn``).
    """
    verdicts: list[EnforcementVerdict] = []
    for rule in ruleset.rules_for(span_type, action_type):
        fn = get_evaluator(rule.evaluator)
        try:
            outcome = fn(attributes, compliance, rule.params)
        except Exception as exc:  # never let an evaluator crash the engine
            outcome = EvalOutcome("warn", f"evaluator {rule.evaluator!r} raised: {exc}")
        result = rule.on_fail if outcome.result == "fail" else outcome.result
        verdicts.append(
            EnforcementVerdict(
                rule_id=rule.id,
                result=result,
                policy_version=ruleset.version,
                reason=outcome.reason,
            )
        )
    return verdicts


def run_advisory(spans: Iterable[Span], ruleset: EnforcementRuleSet) -> list[tuple[Span, EnforcementVerdict]]:
    """Evaluate every gated span against the ruleset; return (span, verdict) pairs."""
    results: list[tuple[Span, EnforcementVerdict]] = []
    for span in spans:
        if span.type not in GATED_TYPES:
            continue
        action_type = getattr(span.attributes, "action_type", None)
        for verdict in evaluate_rules(
            ruleset,
            attributes=span.attributes,
            compliance=span.compliance,
            span_type=span.type,
            action_type=action_type,
        ):
            results.append((span, verdict))
    return results


# ---- ingest-boundary integration -----------------------------------------

_cache: tuple[str, float, EnforcementRuleSet] | None = None


def _cached_ruleset(path: str) -> EnforcementRuleSet:
    """Load + cache the ruleset, refreshing when the file's mtime changes."""
    global _cache
    mtime = os.path.getmtime(path)
    if _cache is not None and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    ruleset = load_rules(path)
    _cache = (path, mtime, ruleset)
    return ruleset


def advisory_observe(
    spans: Iterable[Span],
    *,
    rules_path: str | None = None,
) -> list[tuple[Span, EnforcementVerdict]]:
    """Evaluate accepted spans advisorily, log non-pass verdicts, bump metrics.

    The integration entry point the ledger ingest path calls. Disabled (returns
    ``[]``) unless a rule file is configured via the ``rules_path`` argument or
    the ``ENFORCEMENT_RULES_PATH`` environment variable. Never raises.
    """
    path = rules_path or os.environ.get("ENFORCEMENT_RULES_PATH")
    if not path:
        return []
    try:
        ruleset = _cached_ruleset(path)
        results = run_advisory(spans, ruleset)
    except Exception as exc:  # advisory must never break ingest
        log.error("enforcement.advisory_failed", error=str(exc))
        return []

    for span, verdict in results:
        advisory_verdicts.labels(result=verdict.result, span_type=span.type.value).inc()
        if verdict.result != "pass":
            log.info(
                "enforcement.advisory",
                rule_id=verdict.rule_id,
                result=verdict.result,
                reason=verdict.reason,
                span_id=span.span_id,
                trajectory_id=span.trajectory_id,
                end_customer_id=span.compliance.end_customer_id,
                policy_version=verdict.policy_version,
            )
    return results
