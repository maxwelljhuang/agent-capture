"""Section registry and reason-code taxonomy for the Adverse Action Notice.

The registry is the single list of notice sections, their stable ids, their
human titles, and their severity:

* ``required`` — Regulation B mandates this content; absence raises
  :class:`~agent_capture_reporter.errors.IncompleteTrajectoryError`.
* ``expected`` — the notice normally carries this, but it is not legally
  required; absence renders a ``[NOT CAPTURED]`` marker and records an
  ``expected`` gap.
* ``static`` — fixed legal text (the ECOA/FCRA notice language); always
  rendered, never counted toward completeness.

The reason taxonomy maps a model's reason *codes* to consumer-facing Reg B
language. It is keyed by ``prompt_template_version`` so the wording tracks the
reason set the model was actually prompted with at decision time. Open question
Q2 (vendor-default vs. customer-owned taxonomy) is unresolved; v1 ships a
vendor default and renders unknown codes verbatim, flagging them as a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SectionSeverity = Literal["required", "expected", "static"]


@dataclass(frozen=True)
class SectionSpec:
    """Metadata for one notice section."""

    section_id: str
    title: str
    severity: SectionSeverity


# Order here is the canonical section order used by the manifest.
SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("decision", "Action Taken", "required"),
    SectionSpec("principal_reasons", "Principal Reasons for the Adverse Action", "required"),
    SectionSpec("credit_reporting_agency", "Consumer Reporting Agency", "expected"),
    SectionSpec("protected_class_check", "Protected-Class Compliance Check", "expected"),
    SectionSpec("human_review", "Human Review", "expected"),
    SectionSpec("notice_delivery", "Notice Delivery", "expected"),
    SectionSpec("model_governance", "Model Governance", "expected"),
    SectionSpec("ecoa_notice_language", "ECOA Notice", "static"),
    SectionSpec("fcra_statement", "FCRA Disclosure", "static"),
)

SECTIONS_BY_ID: dict[str, SectionSpec] = {s.section_id: s for s in SECTIONS}

# Sections that count toward the completeness score (everything but static text).
_COUNTED = tuple(s.section_id for s in SECTIONS if s.severity != "static")
COUNTED_SECTION_COUNT = len(_COUNTED)


def is_counted(section_id: str) -> bool:
    """Return whether a section contributes to the completeness score."""
    spec = SECTIONS_BY_ID.get(section_id)
    return spec is not None and spec.severity != "static"


# --- reason-code taxonomy ---------------------------------------------------

# Vendor-default consumer-facing reason language, keyed by prompt_template_version.
# Unknown versions fall back to _DEFAULT_REASONS; unknown codes render verbatim.
_DEFAULT_REASONS: dict[str, str] = {
    "high_dti": "Your monthly debt obligations are too high relative to your income.",
    "delinquencies": "Your credit report shows one or more recent delinquent accounts.",
    "low_credit_score": "Your credit score is below our threshold for the credit requested.",
    "insufficient_income": "Your income is insufficient for the amount of credit requested.",
    "insufficient_credit_history": "You have an insufficient length of credit history.",
    "excessive_obligations": "You have excessive obligations in relation to income.",
    "collateral_insufficient": "The value or type of collateral is not sufficient.",
}

_TAXONOMY_BY_VERSION: dict[str, dict[str, str]] = {
    # The loan-scoring prompt the recorder fixture uses.
    "v17": _DEFAULT_REASONS,
}


def resolve_reason(code: str, prompt_template_version: str | None) -> tuple[str, bool]:
    """Resolve a reason code to consumer-facing text.

    Args:
        code: The model's reason code (e.g. ``"high_dti"``).
        prompt_template_version: The prompt version that produced the code,
            used to select the taxonomy in force at decision time.

    Returns:
        A ``(text, known)`` pair. ``known`` is ``False`` when the code was not
        found in the taxonomy — the caller renders ``code`` verbatim and records
        a gap so the missing mapping is auditable.
    """
    table = _TAXONOMY_BY_VERSION.get(prompt_template_version or "", _DEFAULT_REASONS)
    if code in table:
        return table[code], True
    if code in _DEFAULT_REASONS:
        return _DEFAULT_REASONS[code], True
    return code.replace("_", " "), False
