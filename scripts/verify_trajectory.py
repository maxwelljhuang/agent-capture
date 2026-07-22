#!/usr/bin/env python3
"""Verify a recorded trajectory satisfies the reporting contract.

Reads a JSON-lines trajectory and asserts every assertion in
``docs/reporting-fields.md`` holds:

1. Universal per-span: required core fields, compliance, provenance.
2. Per-trajectory: single root, shared trajectory_id, valid hash chain.
3. Loan-denial scenario: the recorded spans contain enough fields to
   generate an ECOA adverse-action notice, SR 11-7 model documentation,
   and BSA/AML SAR support.

Returns exit code 0 on pass, 1 on any failure. Failures are listed in
order encountered with the span_id of the offender.

Usage::

    PYTHONPATH=packages/python/src python scripts/verify_trajectory.py trajectory.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY_PKG = REPO_ROOT / "packages" / "python" / "src"
if str(PY_PKG) not in sys.path:
    sys.path.insert(0, str(PY_PKG))

from agent_capture.schema import Span, SpanType  # noqa: E402
from agent_capture.schema.canonical import content_hash  # noqa: E402


@dataclass
class VerificationReport:
    failures: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, span_id: str | None, reason: str) -> None:
        prefix = f"[span {span_id[:8]}…] " if span_id else ""
        self.failures.append(f"{prefix}{reason}")

    def note(self, line: str) -> None:
        self.info.append(line)


# ---- top-level checks --------------------------------------------------


def verify(spans: list[Span]) -> VerificationReport:
    """Run every check from the reporting wishlist."""
    report = VerificationReport()
    if not spans:
        report.fail(None, "trajectory is empty")
        return report

    _check_universal(spans, report)
    _check_trajectory_structure(spans, report)
    _check_hash_chain(spans, report)
    _check_adverse_action_ready(spans, report)
    _check_sr_11_7_ready(spans, report)
    _check_bsa_aml_ready(spans, report)

    return report


# ---- universal per-span -----------------------------------------------


def _check_universal(spans: list[Span], report: VerificationReport) -> None:
    for s in spans:
        c = s.compliance
        if not c.policy_version_active:
            report.fail(s.span_id, "compliance.policy_version_active empty")
        if not c.agent_version:
            report.fail(s.span_id, "compliance.agent_version empty")
        if not c.end_customer_id:
            report.fail(s.span_id, "compliance.end_customer_id empty")
        if not c.regulatory_regime:
            report.fail(s.span_id, "compliance.regulatory_regime empty")
        if c.retention_class is None:
            report.fail(s.span_id, "compliance.retention_class missing")
        if c.data_classification is None:
            report.fail(s.span_id, "compliance.data_classification missing")
        p = s.provenance
        if not p.content_hash or len(p.content_hash) != 64:
            report.fail(s.span_id, "provenance.content_hash missing or wrong length")
        if not p.schema_version:
            report.fail(s.span_id, "provenance.schema_version missing")
        if s.attributes.kind != s.type.value:
            report.fail(
                s.span_id,
                f"attributes.kind={s.attributes.kind!r} != type={s.type.value!r}",
            )
        if s.status.value == "error" and s.error is None:
            report.fail(s.span_id, "status=error but error is None")


# ---- trajectory structure ---------------------------------------------


def _check_trajectory_structure(spans: list[Span], report: VerificationReport) -> None:
    roots = [s for s in spans if s.parent_span_id is None]
    if len(roots) != 1:
        report.fail(None, f"expected exactly one root span, got {len(roots)}")
    trajectory_ids = {s.trajectory_id for s in spans}
    if len(trajectory_ids) != 1:
        report.fail(None, f"spans span multiple trajectories: {sorted(trajectory_ids)}")
    by_id = {s.span_id: s for s in spans}
    if len(by_id) != len(spans):
        report.fail(None, "duplicate span_ids in trajectory")
    for s in spans:
        if s.parent_span_id is not None and s.parent_span_id not in by_id:
            report.fail(
                s.span_id,
                f"parent_span_id={s.parent_span_id[:8]}… not found in trajectory",
            )


# ---- hash chain -------------------------------------------------------


def _check_hash_chain(spans: list[Span], report: VerificationReport) -> None:
    by_id = {s.span_id: s for s in spans}
    for s in spans:
        # Recomputed content_hash must match the recorded one (modulo redaction
        # which has already been applied — we're hashing what was shipped).
        if content_hash(s) != s.provenance.content_hash:
            report.fail(s.span_id, "content_hash does not match canonical bytes")
        if s.parent_span_id is None:
            if s.provenance.parent_content_hash is not None:
                report.fail(s.span_id, "root span has non-null parent_content_hash")
            continue
        parent = by_id.get(s.parent_span_id)
        if parent is None:
            continue  # already reported above
        if s.provenance.parent_content_hash != parent.provenance.content_hash:
            report.fail(
                s.span_id,
                f"parent_content_hash={s.provenance.parent_content_hash[:12] if s.provenance.parent_content_hash else None}… "
                f"!= parent.content_hash={parent.provenance.content_hash[:12]}…",
            )


# ---- per-report readiness --------------------------------------------


def _check_adverse_action_ready(spans: list[Span], report: VerificationReport) -> None:
    """Per ECOA §1002.9(b)(2) + FCRA §615 — see docs/reporting-fields.md."""
    has_ecoa = any(
        "ECOA" in (r.value for r in s.compliance.regulatory_regime) for s in spans
    )
    if not has_ecoa:
        return  # not an ECOA-applicable trajectory; skip
    model_calls = [s for s in spans if s.type is SpanType.MODEL_CALL]
    if not model_calls:
        report.fail(None, "adverse-action: no model_call span to ground reasons in")
    for mc in model_calls:
        attrs = mc.attributes
        if not getattr(attrs, "prompt_template_version", None):
            report.fail(
                mc.span_id, "adverse-action: model_call missing prompt_template_version"
            )
        if not mc.compliance.model_card_version:
            report.fail(
                mc.span_id,
                "adverse-action: model_call missing compliance.model_card_version",
            )
        if mc.outputs is None:
            report.fail(
                mc.span_id,
                "adverse-action: model_call outputs missing (no decision reasons)",
            )
    retrievals = [s for s in spans if s.type is SpanType.RETRIEVAL]
    if not retrievals:
        report.fail(
            None, "adverse-action: no retrieval span to identify CRA used (FCRA §615)"
        )
    else:
        for r in retrievals:
            if not getattr(r.attributes, "source_identifier", None):
                report.fail(
                    r.span_id, "adverse-action: retrieval missing source_identifier"
                )
    side_effects = [s for s in spans if s.type is SpanType.SIDE_EFFECT]
    if not any(
        getattr(s.attributes, "action_type", "").startswith("document.")
        and getattr(s.attributes, "success", False)
        for s in side_effects
    ):
        report.fail(
            None,
            "adverse-action: no successful document.* side_effect proves notice was sent",
        )
    if not any(s.type is SpanType.POLICY_CHECK for s in spans):
        report.fail(
            None,
            "adverse-action: no policy_check span (need ECOA protected-class evidence)",
        )


def _check_sr_11_7_ready(spans: list[Span], report: VerificationReport) -> None:
    """Federal Reserve SR 11-7 model governance documentation.

    Only enforced when the trajectory declares the SR_11-7 regime — gated the
    same way as the ECOA and BSA/AML checks. A trajectory that doesn't claim
    model-governance scope (e.g. a framework adapter that can't surface prompt
    template identity) is not held to these fields.
    """
    has_sr_11_7 = any(
        "SR_11-7" in (r.value for r in s.compliance.regulatory_regime) for s in spans
    )
    if not has_sr_11_7:
        return
    for mc in (s for s in spans if s.type is SpanType.MODEL_CALL):
        a = mc.attributes
        for field_name in (
            "model_name",
            "model_version",
            "provider",
            "prompt_template_id",
            "prompt_template_version",
            "temperature",
            "max_tokens",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        ):
            if getattr(a, field_name, None) in (None, ""):
                report.fail(mc.span_id, f"SR 11-7: model_call missing {field_name}")
        if mc.inputs is None:
            report.fail(mc.span_id, "SR 11-7: model_call inputs missing")
        if mc.outputs is None:
            report.fail(mc.span_id, "SR 11-7: model_call outputs missing")


def _check_bsa_aml_ready(spans: list[Span], report: VerificationReport) -> None:
    """BSA/AML SAR-supporting fields, only when the trajectory declares the regime."""
    has_bsa = any(
        "BSA_AML" in (r.value for r in s.compliance.regulatory_regime) for s in spans
    )
    if not has_bsa:
        return
    policy_checks = [s for s in spans if s.type is SpanType.POLICY_CHECK]
    if not any(
        getattr(s.attributes, "policy_name", "").startswith("bsa.")
        or getattr(s.attributes, "policy_name", "").startswith("aml.")
        for s in policy_checks
    ):
        report.fail(None, "BSA/AML: no bsa.* or aml.* policy_check in trajectory")
    side_effects = [s for s in spans if s.type is SpanType.SIDE_EFFECT]
    if not side_effects:
        report.fail(None, "BSA/AML: no side_effect — no action taken to report on")


# ---- CLI ---------------------------------------------------------------


def _parse_jsonl(path: Path) -> list[Span]:
    spans: list[Span] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        data = json.loads(raw)
        spans.append(Span.model_validate(data))
    return spans


def _summarize_types(spans: list[Span]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in spans:
        counts[s.type.value] = counts.get(s.type.value, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trajectory", help="Path to a trajectory JSONL file")
    parser.add_argument("--quiet", action="store_true", help="Only print on failure")
    args = parser.parse_args(argv)

    path = Path(args.trajectory)
    if not path.exists():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 2

    try:
        spans = _parse_jsonl(path)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not parse {path}: {exc}", file=sys.stderr)
        return 2

    report = verify(spans)
    counts = _summarize_types(spans)

    if not args.quiet or not report.ok:
        print(f"trajectory: {path}")
        print(f"span count: {len(spans)}  types: {counts}")
        print(f"trajectory_id: {spans[0].trajectory_id if spans else '<empty>'}")

    if report.ok:
        if not args.quiet:
            print("OK — all reporting-fields contracts satisfied.")
        return 0

    print(f"\n{len(report.failures)} failure(s):", file=sys.stderr)
    for line in report.failures:
        print(f"  - {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
