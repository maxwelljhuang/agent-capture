"""Built-in evaluator tests."""

from __future__ import annotations

import pytest
from agent_capture.schema.compliance import (
    ComplianceMetadata,
    DataClassification,
    RetentionClass,
)
from agent_capture.schema.types import HumanApprovalAttributes, SideEffectAttributes

from agent_capture_enforcement.errors import UnknownEvaluatorError
from agent_capture_enforcement.evaluator import EvalOutcome, get_evaluator, register_evaluator


def _comp() -> ComplianceMetadata:
    return ComplianceMetadata(
        policy_version_active="v1",
        agent_version="0.1.0",
        end_customer_id="acme",
        retention_class=RetentionClass.STANDARD,
        data_classification=DataClassification.INTERNAL,
    )


def _side_effect(*, action_type: str = "document.mail", idempotency_key: str | None = None) -> SideEffectAttributes:
    return SideEffectAttributes(
        action_type=action_type,
        target_system="acme.docs",
        idempotency_key=idempotency_key,
        success=True,
    )


def _approval(*, decision: str = "approved", signature: str | None = None) -> HumanApprovalAttributes:
    return HumanApprovalAttributes(
        approver_identity="user:a",
        approver_role="underwriter",
        decision=decision,  # type: ignore[arg-type]
        decision_timestamp="2026-06-03T00:00:00Z",
        artifact_reviewed="sha256:" + "f" * 64,
        signature=signature,
    )


def test_require_attribute_pass_and_fail() -> None:
    fn = get_evaluator("require_attribute")
    ok = fn(_side_effect(idempotency_key="k1"), _comp(), {"attribute": "idempotency_key"})
    assert ok == EvalOutcome("pass")
    bad = fn(_side_effect(idempotency_key=None), _comp(), {"attribute": "idempotency_key"})
    assert bad.result == "fail"


def test_action_type_allow_and_deny() -> None:
    fn = get_evaluator("action_type_allowed")
    assert fn(_side_effect(action_type="document.mail"), _comp(), {"allow": ["document.mail"]}).result == "pass"
    assert fn(_side_effect(action_type="payment.wire"), _comp(), {"allow": ["document.mail"]}).result == "fail"
    assert fn(_side_effect(action_type="payment.wire"), _comp(), {"deny": ["payment.wire"]}).result == "fail"


def test_action_type_not_applicable_for_non_side_effect() -> None:
    fn = get_evaluator("action_type_allowed")
    assert fn(_approval(), _comp(), {"allow": ["x"]}).result == "not_applicable"


def test_human_decision_is() -> None:
    fn = get_evaluator("human_decision_is")
    assert fn(_approval(decision="approved"), _comp(), {"decision": "approved"}).result == "pass"
    assert fn(_approval(decision="rejected"), _comp(), {"decision": "approved"}).result == "fail"


def test_unknown_evaluator_raises() -> None:
    with pytest.raises(UnknownEvaluatorError):
        get_evaluator("does_not_exist")


def test_register_evaluator() -> None:
    register_evaluator("test_custom", lambda _a, _c, _p: EvalOutcome("warn", "custom"))
    assert get_evaluator("test_custom")(_side_effect(), _comp(), {}).reason == "custom"
