"""The verdict produced by evaluating one enforcement rule against one span.

A verdict is schema-neutral: it maps onto the existing ``policy_check`` span
type (``PolicyCheckAttributes``) so recording an enforcement decision requires
**no schema change** (the schema is frozen at v1.0.0). The bank's rule-bundle
version rides ``policy_version`` (the enforcement analogue of
``ComplianceMetadata.policy_version_active``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from agent_capture.schema.types import PolicyCheckAttributes

Result = Literal["pass", "fail", "warn", "not_applicable"]


@dataclass(frozen=True)
class EnforcementVerdict:
    """The outcome of evaluating one rule against one gated span."""

    rule_id: str
    result: Result
    policy_version: str
    reason: str = ""
    policy_name: str = "enforcement"
    details: Mapping[str, Any] | None = None

    def to_policy_check_attributes(self) -> PolicyCheckAttributes:
        """Render the verdict as ``policy_check`` span attributes (no schema change)."""
        rule_details: dict[str, Any] = {"rule_id": self.rule_id}
        if self.reason:
            rule_details["reason"] = self.reason
        if self.details:
            rule_details.update(dict(self.details))
        return PolicyCheckAttributes(
            policy_name=self.policy_name,
            policy_version=self.policy_version,
            result=self.result,
            rule_details=rule_details,
        )
