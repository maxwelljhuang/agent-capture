"""Enforcement gate hook (recorder side) — compliance layer 5.

This is the **only** enforcement code that ships inside the agent vendor's
product. It publishes a contract — the :class:`EnforcementGate` Protocol plus
the :class:`GateRequest` / :class:`Verdict` value types — and a process-wide
registry (:func:`set_gate` / :func:`current_gate`). The actual rule engine
lives in the separate ``agent-capture-enforcement`` package (vendor cloud) and
is registered here at the vendor's app startup::

    from agent_capture.enforcement import set_gate
    from agent_capture_enforcement.client import EnforcementClient

    set_gate(EnforcementClient(base_url=..., token=...))

Separation of concerns: this module never imports the engine. The dependency
arrow points one way — engine → this contract.

Kill switch: when no gate is registered (the default), :func:`current_gate`
returns ``None`` and the ``@traced`` decorator skips the gate entirely — zero
added latency, byte-for-byte the same behavior as before enforcement existed.
Only the two "critical/irreversible" span types (:data:`GATED_TYPES` —
``side_effect`` and ``human_approval``) are ever gated; everything else is
advisory-only and handled downstream.
"""

from agent_capture.enforcement.gate import (
    GATED_TYPES,
    EnforcementBlocked,
    EnforcementGate,
    GateRequest,
    Verdict,
    current_gate,
    set_gate,
)

__all__ = [
    "GATED_TYPES",
    "EnforcementBlocked",
    "EnforcementGate",
    "GateRequest",
    "Verdict",
    "current_gate",
    "set_gate",
]
