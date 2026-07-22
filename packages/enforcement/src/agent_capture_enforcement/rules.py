"""Enforcement rule model + loader (bank-authored, versioned, vendor-loaded).

Mirrors the redaction policy loader (`agent_capture.redaction.policy`): the
bank writes the YAML, the vendor loads it, and the bundle ``version`` is the
enforcement analogue of ``ComplianceMetadata.policy_version_active``. Loading
is fail-loud — a malformed ruleset raises :class:`RuleLoadError` at load time.

Example::

    version: "enforcement-lending-v1.0.0"
    rules:
      - id: ecoa.aa_letter.allowlisted_action
        span_type: side_effect
        action_type: ["document.mail"]
        evaluator: action_type_allowed
        params: { allow: ["document.mail", "document.email"] }
        mode: advisory            # advisory | blocking
        failure_mode: fail_to_human   # fail_open | fail_to_human | fail_closed
        on_fail: fail             # the policy_check result emitted on predicate fail
        timeout_ms: 150
        hold_timeout_s: 3600
        timeout_action: abort     # abort | allow (on hold timeout)
        enabled: true
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from agent_capture.schema import SpanType

from agent_capture_enforcement.errors import RuleLoadError
from agent_capture_enforcement.evaluator import known_evaluators

Mode = Literal["advisory", "blocking"]
FailureMode = Literal["fail_open", "fail_to_human", "fail_closed"]
OnFail = Literal["fail", "warn"]
TimeoutAction = Literal["abort", "allow"]

_MODES: frozenset[str] = frozenset({"advisory", "blocking"})
_FAILURE_MODES: frozenset[str] = frozenset({"fail_open", "fail_to_human", "fail_closed"})
_ON_FAIL: frozenset[str] = frozenset({"fail", "warn"})
_TIMEOUT_ACTIONS: frozenset[str] = frozenset({"abort", "allow"})


@dataclass(frozen=True)
class EnforcementRule:
    """One bank-authored rule applied to a gated span type."""

    id: str
    span_type: SpanType
    evaluator: str
    params: Mapping[str, Any] = field(default_factory=dict)
    action_type: tuple[str, ...] = ()
    on_fail: OnFail = "fail"
    mode: Mode = "advisory"
    failure_mode: FailureMode = "fail_to_human"
    timeout_ms: int = 150
    hold_timeout_s: int = 3600
    timeout_action: TimeoutAction = "abort"
    enabled: bool = True


@dataclass(frozen=True)
class EnforcementRuleSet:
    """A loaded, immutable rule bundle. Re-load + bump ``version`` to roll out."""

    version: str
    rules: tuple[EnforcementRule, ...] = ()

    def rules_for(self, span_type: SpanType, action_type: str | None = None) -> list[EnforcementRule]:
        """Rules that apply to ``span_type`` (and ``action_type`` if filtered)."""
        out: list[EnforcementRule] = []
        for r in self.rules:
            if not r.enabled or r.span_type != span_type:
                continue
            if r.action_type and (action_type is None or action_type not in r.action_type):
                continue
            out.append(r)
        return out


def load_rules(path: str | os.PathLike[str]) -> EnforcementRuleSet:
    """Parse a YAML rule file. Raises :class:`RuleLoadError` on malformed input."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuleLoadError(f"Could not read rule file at {path}: {exc}") from exc
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"Could not parse rule YAML at {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise RuleLoadError(f"Rule file at {path} must be a YAML mapping at the root.")
    return parse_rules(doc)


def parse_rules(doc: Mapping[str, Any]) -> EnforcementRuleSet:
    """Build an :class:`EnforcementRuleSet` from a pre-parsed mapping."""
    version = doc.get("version")
    if not isinstance(version, str) or not version:
        raise RuleLoadError("Ruleset must declare a non-empty top-level 'version' string.")

    raw_rules = doc.get("rules") or []
    if not isinstance(raw_rules, list):
        raise RuleLoadError("Ruleset 'rules' must be a list.")

    rules = tuple(_parse_rule(i, r) for i, r in enumerate(raw_rules))
    seen: set[str] = set()
    for r in rules:
        if r.id in seen:
            raise RuleLoadError(f"Duplicate rule id {r.id!r}.")
        seen.add(r.id)
    return EnforcementRuleSet(version=version, rules=rules)


def _parse_rule(index: int, r: Any) -> EnforcementRule:
    if not isinstance(r, dict):
        raise RuleLoadError(f"rules[{index}] must be a mapping.")
    rule_id = r.get("id")
    if not isinstance(rule_id, str) or not rule_id:
        raise RuleLoadError(f"rules[{index}] must declare a non-empty 'id'.")

    span_type = _parse_span_type(rule_id, r.get("span_type"))

    evaluator = r.get("evaluator")
    if not isinstance(evaluator, str) or evaluator not in known_evaluators():
        raise RuleLoadError(
            f"rule {rule_id!r} references unknown evaluator {evaluator!r}. Known: {sorted(known_evaluators())}"
        )

    params = r.get("params") or {}
    if not isinstance(params, dict):
        raise RuleLoadError(f"rule {rule_id!r} 'params' must be a mapping.")

    action_type = _parse_str_tuple(rule_id, "action_type", r.get("action_type"))

    on_fail = _parse_enum(rule_id, "on_fail", r.get("on_fail", "fail"), _ON_FAIL)
    mode = _parse_enum(rule_id, "mode", r.get("mode", "advisory"), _MODES)
    failure_mode = _parse_enum(rule_id, "failure_mode", r.get("failure_mode", "fail_to_human"), _FAILURE_MODES)
    timeout_action = _parse_enum(rule_id, "timeout_action", r.get("timeout_action", "abort"), _TIMEOUT_ACTIONS)

    return EnforcementRule(
        id=rule_id,
        span_type=span_type,
        evaluator=evaluator,
        params=params,
        action_type=action_type,
        on_fail=on_fail,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        failure_mode=failure_mode,  # type: ignore[arg-type]
        timeout_ms=int(r.get("timeout_ms", 150)),
        hold_timeout_s=int(r.get("hold_timeout_s", 3600)),
        timeout_action=timeout_action,  # type: ignore[arg-type]
        enabled=bool(r.get("enabled", True)),
    )


def _parse_span_type(rule_id: str, value: Any) -> SpanType:
    if not isinstance(value, str):
        raise RuleLoadError(f"rule {rule_id!r} must declare a 'span_type' string.")
    try:
        return SpanType(value)
    except ValueError as exc:
        raise RuleLoadError(f"rule {rule_id!r} has invalid span_type {value!r}.") from exc


def _parse_str_tuple(rule_id: str, field_name: str, value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise RuleLoadError(f"rule {rule_id!r} {field_name!r} must be a list of strings.")
    return tuple(str(v) for v in value)


def _parse_enum(rule_id: str, field_name: str, value: Any, allowed: frozenset[str]) -> str:
    sval = str(value)
    if sval not in allowed:
        raise RuleLoadError(f"rule {rule_id!r} {field_name!r}={value!r} not in {sorted(allowed)}.")
    return sval
