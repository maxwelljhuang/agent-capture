"""Aggregate a corpus + governance registry into a Model Inventory.

This is the SR 11-7 analog of ``ecoa/extract.py``, but the unit is a *corpus*
over a *reporting period* joined to a *registry*, not a single trajectory. It is
the only place spans and the registry are read; it emits the view-model and the
aggregate provenance together so the manifest cannot drift from the document.

Gap posture is **inverted** vs ECOA: the deficiencies SR 11-7 exists to catalogue
(ungoverned, unvalidated models) are rendered as findings, never render-blocks.
Only "no inventory can be formed" conditions raise
:class:`~agent_capture_reporter.errors.IncompleteInventoryError`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from agent_capture.schema import Span
from agent_capture.schema.types import ModelCallAttributes

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod
from agent_capture_reporter.common.manifest_base import Citation, ReportGap
from agent_capture_reporter.common.provenance import GapLog
from agent_capture_reporter.errors import IncompleteInventoryError
from agent_capture_reporter.manifest.inventory import ColumnProvenance, ModelEntryProvenance
from agent_capture_reporter.sr_11_7.model import (
    GovernanceInfo,
    ModelEntry,
    ModelInventoryModel,
    UsageMetrics,
)
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceEntry, ModelGovernanceRegistry
from agent_capture_reporter.sr_11_7.sections import (
    COLUMNS,
    GOVERNANCE_COLUMNS,
    NO_CARD,
    NOT_IN_REGISTRY,
    TRACKED_COLUMN_COUNT,
    is_validated,
)

DEFAULT_SAMPLE_SIZE = 10


@dataclass
class InventoryExtraction:
    """The product of inventory extraction: view-model + aggregate provenance."""

    model: ModelInventoryModel
    entries_provenance: list[ModelEntryProvenance]
    gaps: list[ReportGap]
    completeness_score: float
    models_missing_card: list[str] = field(default_factory=list)
    models_missing_registry_entry: list[str] = field(default_factory=list)


@dataclass
class _Usage:
    """Mutable accumulator for one model group during the corpus sweep."""

    spans: list[Span] = field(default_factory=list)
    trajectory_ids: set[str] = field(default_factory=set)


def extract_model_inventory(
    corpus: Corpus,
    registry: ModelGovernanceRegistry,
    period: ReportingPeriod,
    *,
    tenant: str | None = None,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> InventoryExtraction:
    """Build the inventory view-model and aggregate provenance.

    Raises:
        IncompleteInventoryError: empty corpus / no in-period model usage, or an
            ambiguous multi-tenant corpus with no ``tenant`` filter.
    """
    in_period = _collect_in_period_model_calls(corpus, period)
    if not in_period:
        raise IncompleteInventoryError(
            "no_model_usage", "no model_call spans fall within the reporting period; nothing to inventory"
        )

    in_period = _apply_tenant_filter(in_period, tenant)

    # Group by (provider, model_name, model_version).
    groups: dict[tuple[str, str, str | None], _Usage] = {}
    for traj_id, span in in_period:
        key = _model_key_tuple(span)
        acc = groups.setdefault(key, _Usage())
        acc.spans.append(span)
        acc.trajectory_ids.add(traj_id)

    gaplog = GapLog()
    entries: list[ModelEntry] = []
    provenance: list[ModelEntryProvenance] = []
    resolved_cells = 0
    governed_count = 0
    missing_card: list[str] = []
    missing_registry: list[str] = []

    for key in sorted(groups, key=lambda k: (k[0], k[1], k[2] or "")):
        provider, model_name, model_version = key
        model_key = f"{provider}|{model_name}|{model_version}"
        usage = _build_usage(groups[key])
        gov, matched_entry, gov_source = _resolve_governance(registry, provider, model_name, model_version, usage)

        if gov_source == "missing_card":
            gaplog.required(model_key, "model used with no governance card recorded (ungoverned model)")
            missing_card.append(model_key)
        elif gov_source == "no_registry_entry":
            gaplog.expected(
                model_key, f"model card(s) {usage.model_card_versions} not found in the governance registry"
            )
            missing_registry.append(model_key)

        cols, row_resolved = _build_columns(model_key, usage, gov, matched_entry, registry, gaplog)
        resolved_cells += row_resolved

        if usage.tokens_partial:
            gaplog.expected(model_key, "token counts missing on some model_call spans; usage totals are partial")

        governed = gov_source == "registry" and bool(usage.model_card_versions)
        if governed:
            governed_count += 1

        entries.append(
            ModelEntry(
                provider=provider,
                model_name=model_name,
                model_version=model_version,
                model_key=model_key,
                usage=usage,
                governance=gov,
                governed=governed,
            )
        )
        provenance.append(_build_entry_provenance(model_key, groups[key], cols, sample_size))

    total_models = len(entries)
    completeness = resolved_cells / (total_models * TRACKED_COLUMN_COUNT) if total_models else 1.0
    unvalidated = [e.model_key for e in entries if not is_validated(_raw_validation(e))]

    model = ModelInventoryModel(
        tenant=tenant,
        period_start=period.start.date().isoformat(),
        period_end=period.end.date().isoformat(),
        generated_date="",  # stamped by report.py from generated_at, kept reproducible
        entries=entries,
        total_models=total_models,
        governed_models=governed_count,
        ungoverned_models=missing_card,
        unvalidated_models=unvalidated,
        trajectories_scanned=len(corpus),
    )
    return InventoryExtraction(
        model=model,
        entries_provenance=provenance,
        gaps=gaplog.gaps,
        completeness_score=completeness,
        models_missing_card=missing_card,
        models_missing_registry_entry=missing_registry,
    )


# --- corpus sweep -----------------------------------------------------------


def _collect_in_period_model_calls(corpus: Corpus, period: ReportingPeriod) -> list[tuple[str, Span]]:
    """Return (trajectory_id, model_call span) pairs whose start_time is in period."""
    out: list[tuple[str, Span]] = []
    for traj in corpus:
        for span in traj.spans:
            if isinstance(span.attributes, ModelCallAttributes) and period.contains(span.start_time):
                out.append((traj.trajectory_id, span))
    return out


def _apply_tenant_filter(pairs: list[tuple[str, Span]], tenant: str | None) -> list[tuple[str, Span]]:
    tenants = {span.compliance.end_customer_id for _, span in pairs}
    if tenant is not None:
        filtered = [(t, s) for t, s in pairs if s.compliance.end_customer_id == tenant]
        if not filtered:
            raise IncompleteInventoryError(
                "tenant_not_present", f"tenant {tenant!r} has no model usage in the reporting period"
            )
        return filtered
    if len(tenants) > 1:
        raise IncompleteInventoryError(
            "ambiguous_tenant",
            f"corpus spans multiple tenants {sorted(tenants)}; pass a tenant filter to scope the inventory",
        )
    return pairs


def _model_key_tuple(span: Span) -> tuple[str, str, str | None]:
    attrs = span.attributes
    assert isinstance(attrs, ModelCallAttributes)
    if not attrs.model_name:
        return (attrs.provider or "unknown", "unidentified", None)
    return (attrs.provider, attrs.model_name, attrs.model_version)


# --- usage aggregation ------------------------------------------------------


def _build_usage(acc: _Usage) -> UsageMetrics:
    spans = sorted(acc.spans, key=lambda s: s.start_time)
    prompt_versions: list[str] = []
    cards: list[str] = []
    regimes: set[str] = set()
    input_sum = output_sum = total_sum = 0
    tokens_partial = False
    for s in spans:
        a = s.attributes
        assert isinstance(a, ModelCallAttributes)
        if a.prompt_template_version and a.prompt_template_version not in prompt_versions:
            prompt_versions.append(a.prompt_template_version)
        card = s.compliance.model_card_version
        if card and card not in cards:
            cards.append(card)
        regimes.update(r.value for r in s.compliance.regulatory_regime)
        if a.total_tokens is None:
            tokens_partial = True
        else:
            total_sum += a.total_tokens
            input_sum += a.input_tokens or 0
            output_sum += a.output_tokens or 0

    any_tokens = any(
        isinstance(s.attributes, ModelCallAttributes) and s.attributes.total_tokens is not None for s in spans
    )
    return UsageMetrics(
        decision_count=len(spans),
        trajectory_count=len(acc.trajectory_ids),
        input_tokens=input_sum if any_tokens else None,
        output_tokens=output_sum if any_tokens else None,
        total_tokens=total_sum if any_tokens else None,
        tokens_partial=tokens_partial,
        first_seen=spans[0].start_time.date().isoformat(),
        last_seen=spans[-1].start_time.date().isoformat(),
        prompt_versions=prompt_versions,
        model_card_versions=cards,
        regimes=sorted(regimes),
    )


# --- governance join --------------------------------------------------------


def _resolve_governance(
    registry: ModelGovernanceRegistry,
    provider: str,
    model_name: str,
    model_version: str | None,
    usage: UsageMetrics,
) -> tuple[GovernanceInfo, ModelGovernanceEntry | None, str]:
    """Return (GovernanceInfo, matched entry, source) for a model group."""
    if not usage.model_card_versions:
        return (
            GovernanceInfo(validation_status=NO_CARD, source="missing_card"),
            None,
            "missing_card",
        )
    # Try each observed card (card-version precedence), then identity fallback.
    matched: ModelGovernanceEntry | None = None
    for card in usage.model_card_versions:
        matched = registry.match(
            provider=provider, model_name=model_name, model_version=model_version, model_card_version=card
        )
        if matched is not None:
            break
    if matched is None:
        matched = registry.match(
            provider=provider, model_name=model_name, model_version=model_version, model_card_version=None
        )
    if matched is None:
        return (
            GovernanceInfo(validation_status=NOT_IN_REGISTRY, source="no_registry_entry"),
            None,
            "no_registry_entry",
        )
    return (
        GovernanceInfo(
            intended_use=matched.intended_use,
            risk_tier=matched.risk_tier,
            validation_status=matched.validation_status,
            last_validated=matched.last_validated,
            valid_until=matched.valid_until,
            limitations=matched.limitations,
            monitoring=matched.monitoring,
            source="registry",
        ),
        matched,
        "registry",
    )


def _raw_validation(entry: ModelEntry) -> str | None:
    """The validation status for the 'unvalidated' rollup (sentinels count as unvalidated)."""
    status = entry.governance.validation_status
    if status in (NO_CARD, NOT_IN_REGISTRY):
        return None
    return status


# --- per-cell provenance ----------------------------------------------------


def _build_columns(
    model_key: str,
    usage: UsageMetrics,
    gov: GovernanceInfo,
    matched_entry: ModelGovernanceEntry | None,
    registry: ModelGovernanceRegistry,
    gaplog: GapLog,
) -> tuple[list[ColumnProvenance], int]:
    """Build per-column provenance for one row; return (columns, resolved_count)."""
    cols: list[ColumnProvenance] = []
    resolved = 0
    registry_values = {
        "intended_use": gov.intended_use,
        "risk_tier": gov.risk_tier,
        "validation_status": matched_entry.validation_status if matched_entry else None,
        "limitations": gov.limitations,
        "monitoring": gov.monitoring,
    }
    for spec in COLUMNS:
        cid = spec.column_id
        if cid == "identity":
            ok = True
        elif cid == "model_card":
            ok = bool(usage.model_card_versions)
        elif cid == "usage":
            ok = True
        else:  # governance registry column
            ok = bool(registry_values.get(cid))
        ref = None
        if matched_entry and spec.kind == "governance_registry":
            ref = f"{registry.source}#{matched_entry.entry_ref}"
        cols.append(
            ColumnProvenance(
                column_id=cid,
                status="rendered" if ok else "gap",
                provenance_kind=spec.kind,
                field_paths=list(spec.field_paths),
                registry_ref=ref,
            )
        )
        if ok:
            resolved += 1
        elif cid in GOVERNANCE_COLUMNS and gov.source == "registry":
            # Registry matched but this specific governance field was absent.
            gaplog.expected(model_key, f"governance column '{cid}' missing from the registry entry")
    return cols, resolved


def _build_entry_provenance(
    model_key: str,
    acc: _Usage,
    columns: list[ColumnProvenance],
    sample_size: int,
) -> ModelEntryProvenance:
    spans = sorted(acc.spans, key=lambda s: s.start_time)
    hashes = sorted({s.provenance.content_hash for s in spans})
    evidence_digest = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
    sample = _sample_spans(spans, sample_size)
    return ModelEntryProvenance(
        model_key=model_key,
        contributing_trajectory_ids=sorted(acc.trajectory_ids),
        contributing_span_count=len(spans),
        evidence_digest=evidence_digest,
        sample_citations=[
            Citation(trajectory_id=s.trajectory_id, span_id=s.span_id, content_hash=s.provenance.content_hash)
            for s in sample
        ],
        sample_size=len(sample),
        citations_truncated=len(sample) < len(spans),
        columns=columns,
    )


def _sample_spans(spans_sorted: list[Span], sample_size: int) -> list[Span]:
    """Pick up to sample_size spans, always including the earliest and latest."""
    count = len(spans_sorted)
    if count <= sample_size or sample_size <= 1:
        return spans_sorted if count <= sample_size else [spans_sorted[0]]
    step = (count - 1) / (sample_size - 1)
    idxs = sorted({round(i * step) for i in range(sample_size)})
    return [spans_sorted[i] for i in idxs]
