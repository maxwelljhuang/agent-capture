"""Extract an :class:`AdverseActionModel` and its provenance from a trajectory.

This module is the *only* place span content is read. It produces three things
together, so the audit manifest can never drift from what the notice shows:

1. the resolved :class:`AdverseActionModel` the template renders,
2. one :class:`SectionProvenance` per notice section (naming the source spans'
   ``content_hash``es), and
3. the list of :class:`TrajectoryGap`s.

The hybrid gap posture lives here: a missing ``required`` section raises
:class:`IncompleteTrajectoryError` (no notice is produced); a missing
``expected`` section becomes a ``gap`` SectionProvenance plus a ``TrajectoryGap``
so the template can mark it ``[NOT CAPTURED]``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agent_capture.schema import Span, SpanType
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
)

from agent_capture_reporter.common.manifest_base import ReportGap
from agent_capture_reporter.common.provenance import GapLog
from agent_capture_reporter.ecoa.model import (
    AdverseActionModel,
    CreditReportingAgency,
    HumanReview,
    ModelGovernance,
    NoticeDelivery,
    PrincipalReason,
    ProtectedClassCheck,
)
from agent_capture_reporter.ecoa.sections import (
    SECTIONS_BY_ID,
    is_counted,
    resolve_reason,
)
from agent_capture_reporter.errors import IncompleteTrajectoryError
from agent_capture_reporter.manifest.schema import SectionProvenance
from agent_capture_reporter.trajectory import Trajectory

_DENY_TERMS = {"deny", "denied", "decline", "declined", "reject", "rejected"}
_COUNTEROFFER_TERMS = {"approve_with_conditions", "counteroffer", "counter_offer", "conditional_approval"}


@dataclass
class ExtractionResult:
    """The product of extraction: a view-model plus its full provenance."""

    model: AdverseActionModel
    sections: list[SectionProvenance]
    gaps: list[ReportGap]
    completeness_score: float


def _digest(text: str) -> str:
    """SHA-256 of the exact text placed in the notice for a section."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _action_statement(decision: str) -> str:
    """Map a raw decision outcome to consumer-facing Reg B action language."""
    normalized = decision.strip().lower()
    if normalized in _DENY_TERMS:
        return "Your application for credit was denied."
    if normalized in _COUNTEROFFER_TERMS:
        # Counteroffer wording is only partially handled in v1 (open question Q3).
        return "Your application for credit was approved on terms other than those you requested."
    return f"The action taken on your application: {decision}."


class _Builder:
    """Accumulates sections, gaps, and missing-required ids during extraction."""

    def __init__(self) -> None:
        self.sections: list[SectionProvenance] = []
        self._gaplog = GapLog()
        self._rendered_counted = 0

    @property
    def gaps(self) -> list[ReportGap]:
        return self._gaplog.gaps

    @property
    def missing_required(self) -> list[str]:
        return self._gaplog.required_scopes

    def rendered(
        self,
        section_id: str,
        *,
        spans: list[Span],
        field_paths: list[str],
        value_text: str,
    ) -> None:
        spec = SECTIONS_BY_ID[section_id]
        self.sections.append(
            SectionProvenance(
                section_id=section_id,
                section_title=spec.title,
                status="rendered",
                source_span_ids=[s.span_id for s in spans],
                source_content_hashes=[s.provenance.content_hash for s in spans],
                field_paths=field_paths,
                rendered_value_digest=_digest(value_text),
            )
        )
        if is_counted(section_id):
            self._rendered_counted += 1

    def gap(self, section_id: str, reason: str) -> None:
        spec = SECTIONS_BY_ID[section_id]
        self.sections.append(
            SectionProvenance(
                section_id=section_id,
                section_title=spec.title,
                status="gap",
                rendered_value_digest=None,
            )
        )
        # Static sections never gap (they are fixed legal text); default defensively.
        severity = spec.severity if spec.severity in ("required", "expected") else "expected"
        self._gaplog.add(section_id, severity, reason)

    def note_gap(self, section_id: str, reason: str) -> None:
        """Record a gap for a section that still rendered (e.g. unmapped reason code)."""
        self._gaplog.expected(section_id, reason)

    def completeness(self) -> float:
        from agent_capture_reporter.ecoa.sections import COUNTED_SECTION_COUNT

        if COUNTED_SECTION_COUNT == 0:
            return 1.0
        return self._rendered_counted / COUNTED_SECTION_COUNT


def extract_adverse_action(trajectory: Trajectory) -> ExtractionResult:
    """Build the notice view-model and provenance from a trajectory.

    Raises:
        IncompleteTrajectoryError: If a Regulation B legally-required element
            (the decision, or the principal reasons) is absent.
    """
    b = _Builder()
    root = trajectory.root

    # --- decision (required) ------------------------------------------------
    decision_value, decision_spans, decision_paths, rationale = _extract_decision(trajectory)
    action_statement = ""
    if decision_value is None:
        b.gap("decision", "no planner_step chosen_option or decision outcome found in the trajectory")
    else:
        action_statement = _action_statement(decision_value)
        b.rendered(
            "decision",
            spans=decision_spans,
            field_paths=decision_paths,
            value_text=action_statement,
        )

    # --- principal reasons (required) ---------------------------------------
    model_call = _select_model_call(trajectory)
    principal_reasons: list[PrincipalReason] = []
    model_governance: ModelGovernance | None = None
    if model_call is None:
        b.gap(
            "principal_reasons",
            "no model_call span with primary_reason/secondary_reasons in outputs",
        )
    else:
        principal_reasons, unknown_codes = _extract_reasons(model_call)
        if not principal_reasons:
            b.gap("principal_reasons", "model_call outputs carried no reason codes")
        else:
            b.rendered(
                "principal_reasons",
                spans=[model_call],
                field_paths=[
                    "model_call.outputs.primary_reason",
                    "model_call.outputs.secondary_reasons",
                    "model_call.attributes.prompt_template_version",
                ],
                value_text="\n".join(f"{r.code}:{r.text}" for r in principal_reasons),
            )
            if unknown_codes:
                b.note_gap(
                    "principal_reasons",
                    f"reason code(s) not in taxonomy, rendered verbatim: {', '.join(sorted(unknown_codes))}",
                )
        model_governance = _extract_model_governance(b, model_call)

    # Required content gate — fail loudly before producing a model.
    if b.missing_required:
        raise IncompleteTrajectoryError(
            missing=b.missing_required,
            detail=(
                "trajectory lacks Regulation B required content and cannot produce a notice: "
                + ", ".join(b.missing_required)
            ),
        )

    # --- expected sections --------------------------------------------------
    cras = _extract_cras(b, trajectory)
    protected = _extract_protected_class(b, trajectory)
    human = _extract_human_review(b, trajectory)
    delivery = _extract_delivery(b, trajectory)

    # --- static legal text (always present) ---------------------------------
    for static_id in ("ecoa_notice_language", "fcra_statement"):
        spec = SECTIONS_BY_ID[static_id]
        b.sections.append(
            SectionProvenance(
                section_id=static_id,
                section_title=spec.title,
                status="rendered",
                rendered_value_digest=None,
            )
        )

    model = AdverseActionModel(
        creditor_id=root.compliance.end_customer_id,
        agent_version=root.compliance.agent_version,
        date_of_action=_date_of_action(trajectory),
        applicant_reference=root.compliance.subject_id,
        decision=decision_value or "",
        action_statement=action_statement,
        decision_rationale=rationale,
        principal_reasons=principal_reasons,
        credit_reporting_agencies=cras,
        human_review=human,
        notice_delivery=delivery,
        protected_class_check=protected,
        model_governance=model_governance,
    )
    return ExtractionResult(
        model=model,
        sections=b.sections,
        gaps=b.gaps,
        completeness_score=b.completeness(),
    )


# --- per-section extractors -------------------------------------------------


def _extract_decision(
    trajectory: Trajectory,
) -> tuple[str | None, list[Span], list[str], str | None]:
    """Return (decision, source_spans, field_paths, rationale)."""
    root = trajectory.root
    if isinstance(root.attributes, PlannerStepAttributes) and root.attributes.chosen_option:
        return (
            root.attributes.chosen_option,
            [root],
            ["planner_step.attributes.chosen_option"],
            root.attributes.decision_rationale,
        )
    if isinstance(root.outputs, dict) and root.outputs.get("decision"):
        return (str(root.outputs["decision"]), [root], ["planner_step.outputs.decision"], None)
    # Fall back to any planner_step that recorded a chosen_option.
    for ps in trajectory.by_type(SpanType.PLANNER_STEP):
        if isinstance(ps.attributes, PlannerStepAttributes) and ps.attributes.chosen_option:
            return (
                ps.attributes.chosen_option,
                [ps],
                ["planner_step.attributes.chosen_option"],
                ps.attributes.decision_rationale,
            )
    return (None, [], [], None)


def _select_model_call(trajectory: Trajectory) -> Span | None:
    """Pick the model_call whose outputs carry the decision reasons (last wins)."""
    chosen: Span | None = None
    for mc in trajectory.by_type(SpanType.MODEL_CALL):
        out = mc.outputs
        if isinstance(out, dict) and (out.get("primary_reason") or out.get("secondary_reasons")):
            chosen = mc
    return chosen


def _extract_reasons(model_call: Span) -> tuple[list[PrincipalReason], set[str]]:
    out = model_call.outputs
    if not isinstance(out, dict):
        return [], set()
    version = (
        model_call.attributes.prompt_template_version
        if isinstance(model_call.attributes, ModelCallAttributes)
        else None
    )
    codes: list[str] = []
    primary = out.get("primary_reason")
    if primary:
        codes.append(str(primary))
    secondary = out.get("secondary_reasons")
    if isinstance(secondary, list):
        codes.extend(str(c) for c in secondary)

    reasons: list[PrincipalReason] = []
    unknown: set[str] = set()
    for code in codes:
        text, known = resolve_reason(code, version)
        if not known:
            unknown.add(code)
        reasons.append(PrincipalReason(code=code, text=text, known=known))
    return reasons, unknown


def _extract_model_governance(b: _Builder, model_call: Span) -> ModelGovernance | None:
    attrs = model_call.attributes
    if not isinstance(attrs, ModelCallAttributes):
        return None
    gov = ModelGovernance(
        model_name=attrs.model_name,
        model_version=attrs.model_version,
        provider=attrs.provider,
        model_card_version=model_call.compliance.model_card_version,
        prompt_template_id=attrs.prompt_template_id,
        prompt_template_version=attrs.prompt_template_version,
    )
    b.rendered(
        "model_governance",
        spans=[model_call],
        field_paths=[
            "model_call.attributes.model_name",
            "model_call.attributes.model_version",
            "model_call.compliance.model_card_version",
        ],
        value_text=f"{gov.model_name}|{gov.model_version}|{gov.model_card_version}",
    )
    return gov


def _extract_cras(b: _Builder, trajectory: Trajectory) -> list[CreditReportingAgency]:
    cras: list[CreditReportingAgency] = []
    spans: list[Span] = []
    for r in trajectory.by_type(SpanType.RETRIEVAL):
        if isinstance(r.attributes, RetrievalAttributes) and r.attributes.source_identifier:
            cras.append(CreditReportingAgency(source_identifier=r.attributes.source_identifier))
            spans.append(r)
    if cras:
        b.rendered(
            "credit_reporting_agency",
            spans=spans,
            field_paths=["retrieval.attributes.source_identifier"],
            value_text="|".join(c.source_identifier for c in cras),
        )
    else:
        b.gap("credit_reporting_agency", "no retrieval span identifies a consumer reporting agency (FCRA §615)")
    return cras


def _extract_protected_class(b: _Builder, trajectory: Trajectory) -> ProtectedClassCheck | None:
    for pc in trajectory.by_type(SpanType.POLICY_CHECK):
        if isinstance(pc.attributes, PolicyCheckAttributes) and pc.attributes.policy_name.startswith(
            "ecoa.protected_class"
        ):
            check = ProtectedClassCheck(
                policy_name=pc.attributes.policy_name,
                policy_version=pc.attributes.policy_version,
                result=pc.attributes.result,
            )
            b.rendered(
                "protected_class_check",
                spans=[pc],
                field_paths=["policy_check.attributes.result", "policy_check.attributes.rule_details"],
                value_text=f"{check.policy_name}|{check.result}",
            )
            return check
    b.gap("protected_class_check", "no ecoa.protected_class.* policy_check span in the trajectory")
    return None


def _extract_human_review(b: _Builder, trajectory: Trajectory) -> HumanReview | None:
    for h in trajectory.by_type(SpanType.HUMAN_APPROVAL):
        if isinstance(h.attributes, HumanApprovalAttributes):
            review = HumanReview(
                approver_identity=h.attributes.approver_identity,
                approver_role=h.attributes.approver_role,
                decision=h.attributes.decision,
                decision_timestamp=h.attributes.decision_timestamp,
            )
            b.rendered(
                "human_review",
                spans=[h],
                field_paths=[
                    "human_approval.attributes.approver_identity",
                    "human_approval.attributes.approver_role",
                    "human_approval.attributes.decision",
                ],
                value_text=f"{review.approver_role}|{review.decision}|{review.decision_timestamp}",
            )
            return review
    b.gap("human_review", "no human_approval span — decision recorded as fully automated")
    return None


def _extract_delivery(b: _Builder, trajectory: Trajectory) -> NoticeDelivery | None:
    document_effects = [
        s
        for s in trajectory.by_type(SpanType.SIDE_EFFECT)
        if isinstance(s.attributes, SideEffectAttributes) and s.attributes.action_type.startswith("document.")
    ]
    if not document_effects:
        b.gap("notice_delivery", "no document.* side_effect proves the notice was sent")
        return None
    # Prefer a successful send if more than one document.* effect exists.
    chosen = next((s for s in document_effects if _side_effect_success(s)), document_effects[-1])
    attrs = chosen.attributes
    assert isinstance(attrs, SideEffectAttributes)
    delivery = NoticeDelivery(
        action_type=attrs.action_type,
        target_system=attrs.target_system,
        success=attrs.success,
        idempotency_key=attrs.idempotency_key,
    )
    b.rendered(
        "notice_delivery",
        spans=[chosen],
        field_paths=[
            "side_effect.attributes.action_type",
            "side_effect.attributes.success",
            "side_effect.attributes.idempotency_key",
        ],
        value_text=f"{delivery.action_type}|{delivery.success}|{delivery.idempotency_key}",
    )
    if not delivery.success:
        b.note_gap("notice_delivery", "notice send side_effect was recorded but did not succeed")
    return delivery


def _side_effect_success(span: Span) -> bool:
    return isinstance(span.attributes, SideEffectAttributes) and span.attributes.success


def _date_of_action(trajectory: Trajectory) -> str:
    """Date of the adverse action: the human decision timestamp if present, else root end_time."""
    for h in trajectory.by_type(SpanType.HUMAN_APPROVAL):
        if isinstance(h.attributes, HumanApprovalAttributes) and h.attributes.decision_timestamp:
            return h.attributes.decision_timestamp[:10]
    return trajectory.root.end_time.date().isoformat()
