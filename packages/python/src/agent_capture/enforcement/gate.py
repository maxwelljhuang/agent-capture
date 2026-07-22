"""The enforcement gate contract + process-wide registry (recorder side).

The gate is consulted by :mod:`agent_capture.instrumentation.decorator` at the
pre-action seam — after a span is opened but *before* the host's side-effecting
function runs — for the two gated span types only. It can let the action run
(``allow``), or stop it (``block``), the latter being the single deliberate
exception to the cardinal rule "the agent always wins" (and only for the two
gated, latency-tolerant, regulator-relevant span types).

Decision contract from the decorator's perspective is **terminal**: the gate
returns ``allow`` or ``block``. A ``hold`` (fail-to-human) is resolved *inside*
the gate implementation (it blocks/awaits the human decision and then returns
``allow``/``block``); the ``hold`` value exists for the verdict the cloud
service hands the client. If a raw ``hold`` ever reaches the decorator it is
treated as ``block`` (fail-safe for an irreversible step).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, runtime_checkable

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.schema import ComplianceMetadata, SpanType
from agent_capture.schema.types import TypedAttributes

if TYPE_CHECKING:
    from agent_capture.span.builder import OpenSpan

# The two span types enforcement may gate: the steps that touch the
# irreversible outside world or a human. Identical *by design* to
# ``exporter.queue._CRITICAL_TYPES`` (the recorder's never-drop set);
# ``tests/unit/test_enforcement_gate.py`` asserts they never drift apart.
GATED_TYPES: Final[frozenset[SpanType]] = frozenset(
    {SpanType.HUMAN_APPROVAL, SpanType.SIDE_EFFECT},
)

Decision = Literal["allow", "hold", "block"]


@dataclass(frozen=True)
class Verdict:
    """The outcome of evaluating enforcement rules against one gated span."""

    decision: Decision
    reason: str = ""
    policy_name: str = "enforcement"
    policy_version: str = "unknown"
    rule_id: str = ""
    rule_details: Mapping[str, Any] | None = None
    hold_id: str | None = None


@dataclass(frozen=True)
class GateRequest:
    """The immutable snapshot of a gated span handed to the gate.

    v1 carries only the immediate span (no trajectory ancestry — that is a v2
    concern that requires the cloud service to query the ledger).
    """

    span_type: SpanType
    name: str
    trajectory_id: str
    span_id: str
    parent_span_id: str | None
    attributes: TypedAttributes
    compliance: ComplianceMetadata
    inputs: Any | None = None


@runtime_checkable
class EnforcementGate(Protocol):
    """The contract a registered enforcement engine client must satisfy.

    Implementations live in the ``agent-capture-enforcement`` package, never
    here. Both methods must return a *terminal* verdict (``allow``/``block``);
    holds are resolved internally.
    """

    def evaluate(self, request: GateRequest) -> Verdict: ...

    async def evaluate_async(self, request: GateRequest) -> Verdict: ...


class EnforcementBlocked(Exception):
    """Raised into the host when enforcement blocks a gated action.

    The single, deliberate exception to the recorder's cardinal rule. It is
    host-visible by design, only ever raised for the two gated span types, and
    only when a fail-closed rule (or a rejected/timed-out human review)
    prevents an irreversible action from running. Vendors enabling a blocking
    rule must handle it.
    """

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        detail = verdict.reason or "blocked by enforcement policy"
        super().__init__(
            f"{detail} (rule={verdict.rule_id or '?'}, policy={verdict.policy_name}@{verdict.policy_version})"
        )


# ---- process-wide gate registry (mirrors agent_capture._internal.runtime) ----

_gate: EnforcementGate | None = None


def set_gate(gate: EnforcementGate | None) -> None:
    """Register the process-wide enforcement gate. Pass ``None`` to uninstall.

    The vendor calls this once at app startup with an engine client. Leaving
    it unset (the default) is the global kill switch: enforcement is a no-op.
    """
    global _gate
    _gate = gate


def current_gate() -> EnforcementGate | None:
    """Return the registered enforcement gate, or ``None`` if enforcement is off."""
    return _gate


# ---- decorator-facing helpers --------------------------------------------


def _build_request(open_span: OpenSpan) -> GateRequest:
    return GateRequest(
        span_type=open_span.type,
        name=open_span.name,
        trajectory_id=open_span.trajectory_id,
        span_id=open_span.span_id,
        parent_span_id=open_span.parent_span_id,
        attributes=open_span.attributes,
        compliance=open_span.compliance,
        inputs=open_span.inputs,
    )


def evaluate_gate(open_span: OpenSpan) -> Verdict | None:
    """Consult the registered gate for a gated span (sync path).

    Returns ``None`` when enforcement should not act (no gate registered, or
    the span type is not gated) — the caller proceeds normally. Otherwise
    returns the gate's :class:`Verdict`.

    Never raises: an internal gate *failure* is logged and treated as
    fail-open (returns an ``allow`` verdict), because a gate bug is not an
    explicit fail-closed decision and must never crash the host. The gate
    implementation is responsible for turning unreachable/timeout into the
    rule's configured failure mode — including a ``block`` verdict for
    fail-closed rules.
    """
    gate = current_gate()
    if gate is None or open_span.type not in GATED_TYPES:
        return None
    try:
        return gate.evaluate(_build_request(open_span))
    except Exception as exc:
        log_error(ErrorCode.AC501, "enforcement gate evaluate failed: %s", exc)
        return Verdict(decision="allow", reason="gate error: fail-open")


async def evaluate_gate_async(open_span: OpenSpan) -> Verdict | None:
    """Async counterpart of :func:`evaluate_gate` (does not block the loop)."""
    gate = current_gate()
    if gate is None or open_span.type not in GATED_TYPES:
        return None
    try:
        return await gate.evaluate_async(_build_request(open_span))
    except Exception as exc:
        log_error(ErrorCode.AC501, "enforcement gate evaluate_async failed: %s", exc)
        return Verdict(decision="allow", reason="gate error: fail-open")


def is_blocking(verdict: Verdict | None) -> bool:
    """Whether the decorator should stop the host action.

    Proceed only on an explicit ``allow`` (or no verdict). Anything else —
    ``block`` or an unresolved ``hold`` — stops the action (fail-safe).
    """
    return verdict is not None and verdict.decision != "allow"
