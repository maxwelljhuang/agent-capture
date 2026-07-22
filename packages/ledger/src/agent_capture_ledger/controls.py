"""Compliance control catalog for ``GET /stats.controls``.

A *control* is a check derivable from span presence / regime over a trajectory:
``passing/total`` of in-scope trajectories satisfy it. Bank-/vendor-authored,
loaded from YAML (``LEDGER_CONTROLS_PATH``) with a built-in default catalog.
The evaluation lives in ``SpanRepo.evaluate_controls``; this module is just the
catalog model + loader. See docs/ledger-additive-plan.md §P0.

A condition matches a trajectory when it has a span satisfying ALL of its set
fields (``regime`` and/or ``has_span_type``). A control's ``total`` is the count
of trajectories matching ``scope``; ``passing`` is those also matching
``pass_when``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent_capture_ledger.config import get_settings


@dataclass(frozen=True)
class ControlCondition:
    regime: str | None = None
    has_span_type: str | None = None

    def __post_init__(self) -> None:
        if self.regime is None and self.has_span_type is None:
            raise ValueError("control condition needs at least one of regime / has_span_type")


@dataclass(frozen=True)
class Control:
    regime: str
    key: str
    label: str
    scope: ControlCondition
    pass_when: ControlCondition


# Built-in default catalog — Kelp's proposed ECOA/FCRA set. Predicates are
# deliberately simple (span-presence) and tunable via LEDGER_CONTROLS_PATH.
DEFAULT_CATALOG: tuple[Control, ...] = (
    Control(
        regime="ECOA",
        key="adverse_action",
        label="Adverse-action decision recorded",
        scope=ControlCondition(regime="ECOA"),
        pass_when=ControlCondition(has_span_type="policy_check"),
    ),
    Control(
        regime="FCRA",
        key="consumer_report",
        label="Consumer report consulted",
        scope=ControlCondition(regime="FCRA"),
        pass_when=ControlCondition(has_span_type="tool_call"),
    ),
    Control(
        regime="ECOA",
        key="model_rationale",
        label="Model rationale documented",
        scope=ControlCondition(has_span_type="model_call"),
        pass_when=ControlCondition(has_span_type="policy_check"),
    ),
    Control(
        regime="ECOA",
        key="human_review",
        label="Human review present",
        scope=ControlCondition(regime="ECOA"),
        pass_when=ControlCondition(has_span_type="human_approval"),
    ),
)


def _parse_condition(raw: Any, where: str) -> ControlCondition:
    if not isinstance(raw, dict):
        raise ValueError(f"control {where} must be a mapping")
    return ControlCondition(regime=raw.get("regime"), has_span_type=raw.get("has_span_type"))


def parse_catalog(doc: Any) -> tuple[Control, ...]:
    if not isinstance(doc, dict) or not isinstance(doc.get("controls"), list):
        raise ValueError("control catalog must be a mapping with a 'controls' list")
    out: list[Control] = []
    for entry in doc["controls"]:
        if not isinstance(entry, dict):
            raise ValueError("each control must be a mapping")
        for required in ("regime", "key", "label", "scope", "pass_when"):
            if required not in entry:
                raise ValueError(f"control missing required field {required!r}")
        out.append(
            Control(
                regime=str(entry["regime"]),
                key=str(entry["key"]),
                label=str(entry["label"]),
                scope=_parse_condition(entry["scope"], f"{entry['key']}.scope"),
                pass_when=_parse_condition(entry["pass_when"], f"{entry['key']}.pass_when"),
            )
        )
    return tuple(out)


def load_catalog(path: str | Path) -> tuple[Control, ...]:
    return parse_catalog(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def current_catalog() -> tuple[Control, ...]:
    """The active catalog: ``LEDGER_CONTROLS_PATH`` if set, else the default."""
    path = get_settings().controls_path
    return load_catalog(path) if path is not None else DEFAULT_CATALOG
