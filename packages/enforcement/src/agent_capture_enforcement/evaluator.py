"""Rule evaluators — named, immediate-span predicates.

An evaluator is a pure function over one span's typed attributes + compliance
metadata, returning :class:`EvalOutcome`. v1 is deliberately immediate-span
only: no trajectory ancestry (that requires the cloud service to query the
ledger and is a v2 concern). New evaluators register via
:func:`register_evaluator`, mirroring the redaction strategy registry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from agent_capture.schema import ComplianceMetadata
from agent_capture.schema.types import TypedAttributes

from agent_capture_enforcement.errors import UnknownEvaluatorError

Result = Literal["pass", "fail", "warn", "not_applicable"]


@dataclass(frozen=True)
class EvalOutcome:
    """The raw result of running an evaluator predicate."""

    result: Result
    reason: str = ""


EvaluatorFn = Callable[[TypedAttributes, ComplianceMetadata, Mapping[str, Any]], EvalOutcome]

_EVALUATORS: dict[str, EvaluatorFn] = {}


def register_evaluator(name: str, fn: EvaluatorFn) -> None:
    """Register an evaluator under ``name`` (overwrites an existing one)."""
    _EVALUATORS[name] = fn


def get_evaluator(name: str) -> EvaluatorFn:
    """Return the evaluator registered under ``name``.

    Raises:
        UnknownEvaluatorError: if no evaluator is registered under ``name``.
    """
    try:
        return _EVALUATORS[name]
    except KeyError as exc:
        raise UnknownEvaluatorError(f"Unknown evaluator {name!r}. Registered: {sorted(_EVALUATORS)}") from exc


def known_evaluators() -> frozenset[str]:
    """Return the set of registered evaluator names."""
    return frozenset(_EVALUATORS)


# ---- built-in evaluators (immediate-span) --------------------------------


def _require_attribute(attrs: TypedAttributes, _comp: ComplianceMetadata, params: Mapping[str, Any]) -> EvalOutcome:
    """Pass iff the named attribute is present and truthy on the span's typed payload.

    params: ``{"attribute": "idempotency_key"}``. Use for "every payment must
    carry an idempotency_key" or "every human_approval must carry a signature".
    """
    attribute = str(params.get("attribute", ""))
    if not attribute:
        return EvalOutcome("not_applicable", "no 'attribute' configured")
    value = getattr(attrs, attribute, None)
    if value:
        return EvalOutcome("pass")
    return EvalOutcome("fail", f"required attribute {attribute!r} is missing or empty")


def _action_type_allowed(attrs: TypedAttributes, _comp: ComplianceMetadata, params: Mapping[str, Any]) -> EvalOutcome:
    """Allow/deny a side_effect by its ``action_type``.

    params: ``{"allow": [...]}`` and/or ``{"deny": [...]}``.
    """
    action_type = getattr(attrs, "action_type", None)
    if action_type is None:
        return EvalOutcome("not_applicable", "span has no action_type")
    deny = [str(x) for x in params.get("deny", [])]
    if action_type in deny:
        return EvalOutcome("fail", f"action_type {action_type!r} is on the denylist")
    allow = params.get("allow")
    if allow is not None and action_type not in [str(x) for x in allow]:
        return EvalOutcome("fail", f"action_type {action_type!r} is not on the allowlist")
    return EvalOutcome("pass")


def _human_decision_is(attrs: TypedAttributes, _comp: ComplianceMetadata, params: Mapping[str, Any]) -> EvalOutcome:
    """Pass iff a human_approval's ``decision`` equals the expected value.

    params: ``{"decision": "approved"}``.
    """
    expected = str(params.get("decision", "approved"))
    decision = getattr(attrs, "decision", None)
    if decision is None:
        return EvalOutcome("not_applicable", "span has no decision")
    if decision == expected:
        return EvalOutcome("pass")
    return EvalOutcome("fail", f"decision {decision!r} != required {expected!r}")


def _always(result: Result) -> EvaluatorFn:
    def _fn(_a: TypedAttributes, _c: ComplianceMetadata, _p: Mapping[str, Any]) -> EvalOutcome:
        return EvalOutcome(result, f"always_{result}")

    return _fn


register_evaluator("require_attribute", _require_attribute)
register_evaluator("action_type_allowed", _action_type_allowed)
register_evaluator("human_decision_is", _human_decision_is)
register_evaluator("always_pass", _always("pass"))
register_evaluator("always_fail", _always("fail"))
