"""Schema-aware redaction — field-name routing.

Walks dict-shaped payloads and routes values to a strategy when their
key matches a policy ``field_rule``. This is the cheap, exact path: it
runs in microseconds and never has false positives.

When a value is a primitive (str/int/etc.) and its key matched a rule,
the strategy is applied directly to ``str(value)``. When the value is
itself a dict or list, the walker recurses with the matched strategy
applied to *every* leaf — the customer's "this whole sub-tree is
sensitive" intent.
"""

from __future__ import annotations

from typing import Any

from agent_capture.redaction.policy import Policy
from agent_capture.redaction.strategies import RedactionStrategy


class SchemaAwareRedactor:
    """Redact values keyed by name per the policy's ``field_rules``."""

    def __init__(self, *, policy: Policy) -> None:
        self._policy = policy

    def redact(self, value: Any) -> Any:
        """Return ``value`` with field-rule redactions applied.

        Does NOT run pattern recognizers — the filter pipeline runs the
        pattern redactor separately so the two layers can be tested and
        toggled independently.
        """
        return self._walk(value, inherited_strategy=None, current_field_type=None)

    def _walk(
        self,
        value: Any,
        *,
        inherited_strategy: RedactionStrategy | None,
        current_field_type: str | None,
    ) -> Any:
        if isinstance(value, dict):
            return {k: self._walk_dict_value(k, v, inherited_strategy=inherited_strategy) for k, v in value.items()}
        if isinstance(value, list):
            return [
                self._walk(
                    v,
                    inherited_strategy=inherited_strategy,
                    current_field_type=current_field_type,
                )
                for v in value
            ]
        if inherited_strategy is None or value is None:
            return value
        # Primitive leaf inside a marked-sensitive sub-tree.
        return inherited_strategy.redact(str(value), field_type=current_field_type or "field")

    def _walk_dict_value(
        self,
        key: str,
        value: Any,
        *,
        inherited_strategy: RedactionStrategy | None,
    ) -> Any:
        strategy = self._policy.strategy_for_field(key)
        if strategy is None:
            # No rule at this level — pass inherited strategy down unchanged.
            return self._walk(
                value,
                inherited_strategy=inherited_strategy,
                current_field_type=None,
            )
        # Rule matched. Apply this strategy to the entire sub-tree below.
        return self._walk(
            value,
            inherited_strategy=strategy,
            current_field_type=key.lower(),
        )
