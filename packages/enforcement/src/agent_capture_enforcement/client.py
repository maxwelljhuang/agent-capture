"""``EnforcementClient`` — the recorder-side gate implementation.

Satisfies the :class:`agent_capture.enforcement.EnforcementGate` Protocol. The
vendor registers it at app startup via
``agent_capture.enforcement.set_gate(EnforcementClient(...))``. On each gated
span it calls the vendor-cloud verdict service synchronously (bounded by a
tight timeout). When the service is unreachable or slow, it consults a **local
failure-mode fallback table** (failure-mode *metadata* only — never rule logic)
so a ``fail_closed`` contract still holds during a cloud outage.

This module logs via the recorder's safelog so a misbehaving client surfaces
the same AC5xx codes; it still never crashes the host (the decorator's
``evaluate_gate`` wrapper is the final backstop).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Literal, cast

import httpx
from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.enforcement import GateRequest, Verdict

_Decision = Literal["allow", "hold", "block"]
_FALLBACK_DECISION: dict[str, _Decision] = {
    "fail_open": "allow",
    "fail_to_human": "hold",
    "fail_closed": "block",
}


class EnforcementClient:
    """A thin synchronous+async client for the verdict service."""

    def __init__(
        self,
        base_url: str = "",
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        async_client: httpx.AsyncClient | None = None,
        timeout_ms: int = 150,
        fallback: Mapping[tuple[str, str], str] | None = None,
        default_failure_mode: str = "fail_open",
        hold_poll_interval_ms: int = 500,
        max_hold_wait_s: int = 3600,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_ms / 1000.0
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client
        self._async_client = async_client
        # (end_customer_id, action_class) -> failure_mode metadata.
        self._fallback = dict(fallback or {})
        self._default_failure_mode = default_failure_mode
        self._poll_interval = hold_poll_interval_ms / 1000.0
        self._max_polls = max(1, int(max_hold_wait_s * 1000 / max(1, hold_poll_interval_ms)))

    # ---- EnforcementGate Protocol ----------------------------------------

    def evaluate(self, request: GateRequest) -> Verdict:
        own = self._client is None
        http = self._client or httpx.Client(base_url=self._base_url, timeout=self._timeout)
        try:
            resp = http.post("/verdict", json=_payload(request), headers=self._headers)
            resp.raise_for_status()
            verdict = _verdict_from(resp.json())
            if verdict.decision == "hold" and verdict.hold_id:
                return self._poll_resolution(http, verdict)
            return verdict
        except (httpx.HTTPError, ValueError) as exc:
            log_error(ErrorCode.AC502, "verdict service call failed: %s", exc)
            return self._fallback_verdict(request)
        finally:
            if own:
                http.close()

    async def evaluate_async(self, request: GateRequest) -> Verdict:
        own = self._async_client is None
        http = self._async_client or httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        try:
            resp = await http.post("/verdict", json=_payload(request), headers=self._headers)
            resp.raise_for_status()
            verdict = _verdict_from(resp.json())
            if verdict.decision == "hold" and verdict.hold_id:
                return await self._poll_resolution_async(http, verdict)
            return verdict
        except (httpx.HTTPError, ValueError) as exc:
            log_error(ErrorCode.AC502, "verdict service async call failed: %s", exc)
            return self._fallback_verdict(request)
        finally:
            if own:
                await http.aclose()

    # ---- fail-to-human: poll the hold's resolution -----------------------

    def _poll_resolution(self, http: httpx.Client, verdict: Verdict) -> Verdict:
        for _ in range(self._max_polls):
            try:
                resp = http.get(f"/holds/{verdict.hold_id}/resolution", headers=self._headers)
                resp.raise_for_status()
                resolved = _resolution_from(resp.json(), verdict)
                if resolved is not None:
                    return resolved
            except httpx.HTTPError as exc:  # transient during a human review window
                log_error(ErrorCode.AC503, "hold resolution poll failed: %s", exc)
            time.sleep(self._poll_interval)
        return _aborted(verdict, "hold wait exceeded")

    async def _poll_resolution_async(self, http: httpx.AsyncClient, verdict: Verdict) -> Verdict:
        for _ in range(self._max_polls):
            try:
                resp = await http.get(f"/holds/{verdict.hold_id}/resolution", headers=self._headers)
                resp.raise_for_status()
                resolved = _resolution_from(resp.json(), verdict)
                if resolved is not None:
                    return resolved
            except httpx.HTTPError as exc:
                log_error(ErrorCode.AC503, "hold resolution poll failed: %s", exc)
            await asyncio.sleep(self._poll_interval)
        return _aborted(verdict, "hold wait exceeded")

    # ---- local fallback (outage path) ------------------------------------

    def _fallback_verdict(self, request: GateRequest) -> Verdict:
        action_class = getattr(request.attributes, "action_type", None) or request.span_type.value
        mode = self._fallback.get((request.compliance.end_customer_id, action_class), self._default_failure_mode)
        decision = _FALLBACK_DECISION.get(mode, "allow")
        return Verdict(
            decision=decision,
            reason=f"verdict service unreachable; local fallback failure_mode={mode}",
            policy_name="enforcement",
            policy_version="fallback",
            rule_id="",
            rule_details={"source": "fallback", "failure_mode": mode},
        )


def _payload(request: GateRequest) -> dict[str, Any]:
    return {
        "span_type": request.span_type.value,
        "name": request.name,
        "trajectory_id": request.trajectory_id,
        "span_id": request.span_id,
        "parent_span_id": request.parent_span_id,
        "attributes": request.attributes.model_dump(mode="json"),
        "compliance": request.compliance.model_dump(mode="json"),
    }


def _verdict_from(data: Mapping[str, Any]) -> Verdict:
    raw = str(data.get("decision", "allow"))
    decision: _Decision = cast(_Decision, raw) if raw in ("allow", "hold", "block") else "allow"
    return Verdict(
        decision=decision,
        reason=str(data.get("reason", "")),
        policy_name=str(data.get("policy_name", "enforcement")),
        policy_version=str(data.get("policy_version", "unknown")),
        rule_id=str(data.get("rule_id", "")),
        hold_id=data.get("hold_id"),
    )


def _resolution_from(data: Mapping[str, Any], verdict: Verdict) -> Verdict | None:
    """Map a hold-resolution poll to a terminal verdict, or None while pending."""
    status = str(data.get("status", "pending"))
    raw = data.get("decision")
    if status == "pending" or raw not in ("allow", "block"):
        return None
    return Verdict(
        decision=cast(_Decision, raw),
        reason=f"hold {status}",
        policy_name=verdict.policy_name,
        policy_version=verdict.policy_version,
        rule_id=verdict.rule_id,
        hold_id=verdict.hold_id,
    )


def _aborted(verdict: Verdict, reason: str) -> Verdict:
    return Verdict(
        decision="block",
        reason=reason,
        policy_name=verdict.policy_name,
        policy_version=verdict.policy_version,
        rule_id=verdict.rule_id,
        hold_id=verdict.hold_id,
    )
