"""Redaction policy — customer-owned, vendor-neutral.

The end customer (the bank) writes the YAML; the vendor ships the SDK
that loads it. The vendor never edits this file. The policy bundle is
versioned and the version is stamped on every span via
:attr:`ComplianceMetadata.policy_version_active`.

Example::

    version: "lending-v2.3.1"
    default_strategy: full
    strategies:
      ssn:
        type: full
      account_number:
        type: hmac
        key_env: AGENT_CAPTURE_HMAC_KEY
      public_score:
        type: pass_through
    field_rules:
      - field_name: ssn
        strategy: ssn
      - field_name: social_security_number
        strategy: ssn
      - field_name: account_number
        strategy: account_number
    pattern_rules:
      - field_type: ssn
        strategy: ssn
      - field_type: routing_number
        strategy: account_number
      - field_type: account_number
        strategy: account_number
      - field_type: micr
        strategy: account_number
      - field_type: date_of_birth
        strategy: ssn

Loading is fail-loud. A malformed policy raises :class:`RedactionError`
at load time — the agent must start with a valid policy or not at all.
A missing policy means *no redaction*, which the SDK treats as an
explicit configuration mistake: callers must pass a policy explicitly
(even if it's :func:`pass_through_policy`) to acknowledge the choice.

On top of the customer policy sits a non-negotiable **redaction floor**
(:data:`_PII_FLOOR_FIELD_TYPES`): recognized-PII field types can never be
passed through in cleartext, even under a pass-through policy. The policy may
choose between full redaction and HMAC fingerprinting for them, never disable
redaction. See :meth:`Policy.strategy_for_pattern`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_capture.errors import RedactionError
from agent_capture.redaction.strategies import (
    FullRedaction,
    HmacFingerprint,
    PassThrough,
    RedactionStrategy,
)

_STRATEGY_BUILDERS: dict[str, Callable[[Mapping[str, Any]], RedactionStrategy]] = {
    "full": lambda _: FullRedaction(),
    "pass_through": lambda _: PassThrough(),
    "hmac": lambda cfg: HmacFingerprint(
        key_env=cfg.get("key_env"),
        key=(cfg["key"].encode("utf-8") if cfg.get("key") else None),
        truncate=int(cfg.get("truncate", 32)),
    ),
}

# The non-negotiable redaction floor. A value that a recognizer flags as one of
# these PII ``field_type``s may NEVER ship in cleartext, whatever the customer
# policy says — a ``pass_through`` for it is coerced up to full redaction. The
# policy may still *choose* HMAC fingerprinting (it just can't disable
# redaction). Recognized PII reaches ``strategy_for_pattern`` only via a
# recognizer match, so flooring there guarantees no recognized PII reaches the
# durable ledger un-redacted. Extend this set when adding PII recognizers.
_PII_FLOOR_FIELD_TYPES: frozenset[str] = frozenset({"ssn", "routing_number", "account_number", "micr", "date_of_birth"})
_FLOOR_STRATEGY: RedactionStrategy = FullRedaction()


@dataclass(frozen=True)
class FieldRule:
    """Match a dict field by name (case-insensitive) and route to a strategy."""

    field_name: str
    strategy: str


@dataclass(frozen=True)
class PatternRule:
    """Match a recognized pattern's field_type and route to a strategy."""

    field_type: str
    strategy: str


@dataclass(frozen=True)
class Policy:
    """A loaded customer policy bundle.

    Instances are immutable after construction. Re-load the file (and
    bump :attr:`version`) to roll out changes.
    """

    version: str
    default_strategy: str
    strategies: Mapping[str, RedactionStrategy]
    field_rules: tuple[FieldRule, ...] = field(default_factory=tuple)
    pattern_rules: tuple[PatternRule, ...] = field(default_factory=tuple)

    # ---- lookups -----------------------------------------------------

    def strategy_for_field(self, field_name: str) -> RedactionStrategy | None:
        """Strategy for an exact dict-field-name rule. ``None`` if no rule fires."""
        lower = field_name.lower()
        for r in self.field_rules:
            if r.field_name.lower() == lower:
                return self._resolve(r.strategy)
        return None

    def strategy_for_pattern(self, field_type: str) -> RedactionStrategy:
        """Strategy for a recognizer's ``field_type``. Falls back to default.

        Enforces the redaction floor (:data:`_PII_FLOOR_FIELD_TYPES`): a
        recognized-PII ``field_type`` can never resolve to :class:`PassThrough`
        (cleartext) — it is coerced to full redaction. HMAC stays allowed.
        """
        resolved: RedactionStrategy
        for r in self.pattern_rules:
            if r.field_type == field_type:
                resolved = self._resolve(r.strategy)
                break
        else:
            resolved = self._resolve(self.default_strategy)
        if field_type in _PII_FLOOR_FIELD_TYPES and isinstance(resolved, PassThrough):
            return _FLOOR_STRATEGY
        return resolved

    def _resolve(self, name: str) -> RedactionStrategy:
        try:
            return self.strategies[name]
        except KeyError as exc:
            raise RedactionError(f"Policy references undefined strategy {name!r}") from exc


def load_policy(path: str | os.PathLike[str]) -> Policy:
    """Parse a YAML policy file. Raises :class:`RedactionError` on malformed input."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RedactionError(f"Could not parse policy YAML at {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise RedactionError(f"Policy at {path} must be a YAML mapping at the root.")
    return _parse(doc)


def parse_policy(doc: Mapping[str, Any]) -> Policy:
    """Build a :class:`Policy` from a pre-parsed mapping (e.g. for tests)."""
    return _parse(doc)


def pass_through_policy(version: str = "pass-through") -> Policy:
    """A near-no-op policy.

    Use only in development or when the customer explicitly classifies
    every field as non-sensitive. The SDK requires callers to pass
    *some* policy so this choice is acknowledged. Note the redaction floor
    still applies: recognized PII (SSN, routing/account number, MICR, DOB) is
    redacted even here — pass-through only governs everything else.
    """
    return Policy(
        version=version,
        default_strategy="pass_through",
        strategies={"pass_through": PassThrough()},
    )


def _parse(doc: Mapping[str, Any]) -> Policy:
    version = doc.get("version")
    if not isinstance(version, str) or not version:
        raise RedactionError("Policy must declare a non-empty top-level 'version' string.")

    raw_strategies = doc.get("strategies") or {}
    if not isinstance(raw_strategies, dict):
        raise RedactionError("Policy 'strategies' must be a mapping.")
    strategies: dict[str, RedactionStrategy] = {}
    for name, cfg in raw_strategies.items():
        if not isinstance(cfg, dict):
            raise RedactionError(f"Strategy {name!r} config must be a mapping.")
        type_ = cfg.get("type")
        if type_ not in _STRATEGY_BUILDERS:
            raise RedactionError(
                f"Strategy {name!r} declares unknown type {type_!r}. Allowed: {sorted(_STRATEGY_BUILDERS)}"
            )
        strategies[name] = _STRATEGY_BUILDERS[type_](cfg)

    default_strategy = doc.get("default_strategy", "full")
    if default_strategy not in strategies:
        # Auto-provide a 'full' default if the customer didn't declare one.
        if default_strategy == "full":
            strategies["full"] = FullRedaction()
        else:
            raise RedactionError(f"default_strategy {default_strategy!r} not present in 'strategies'.")

    raw_field_rules = doc.get("field_rules") or []
    if not isinstance(raw_field_rules, list):
        raise RedactionError("Policy 'field_rules' must be a list.")
    field_rules: tuple[FieldRule, ...] = tuple(_parse_field_rule(r) for r in raw_field_rules)

    raw_pattern_rules = doc.get("pattern_rules") or []
    if not isinstance(raw_pattern_rules, list):
        raise RedactionError("Policy 'pattern_rules' must be a list.")
    pattern_rules: tuple[PatternRule, ...] = tuple(_parse_pattern_rule(r) for r in raw_pattern_rules)

    # Validate that every rule references a known strategy.
    for fr in field_rules:
        if fr.strategy not in strategies:
            raise RedactionError(f"field_rule for {fr.field_name!r} uses undefined strategy {fr.strategy!r}")
    for pr in pattern_rules:
        if pr.strategy not in strategies:
            raise RedactionError(f"pattern_rule for {pr.field_type!r} uses undefined strategy {pr.strategy!r}")

    return Policy(
        version=version,
        default_strategy=default_strategy,
        strategies=strategies,
        field_rules=field_rules,
        pattern_rules=pattern_rules,
    )


def _parse_field_rule(r: Any) -> FieldRule:
    if not isinstance(r, dict) or "field_name" not in r or "strategy" not in r:
        raise RedactionError("field_rule entries must be mappings with 'field_name' and 'strategy'.")
    return FieldRule(field_name=str(r["field_name"]), strategy=str(r["strategy"]))


def _parse_pattern_rule(r: Any) -> PatternRule:
    if not isinstance(r, dict) or "field_type" not in r or "strategy" not in r:
        raise RedactionError("pattern_rule entries must be mappings with 'field_type' and 'strategy'.")
    return PatternRule(field_type=str(r["field_type"]), strategy=str(r["strategy"]))
