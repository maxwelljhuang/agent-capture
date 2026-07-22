"""W3C Trace Context inject/extract for cross-process linking.

The W3C Trace Context recommendation (https://www.w3.org/TR/trace-context/)
defines two HTTP headers:

- ``traceparent``: ``{version}-{trace_id}-{span_id}-{flags}``
  with ``version`` = ``00`` (current), ``trace_id`` = 32 hex chars,
  ``span_id`` = 16 hex chars, ``flags`` = 2 hex chars (``01`` = sampled).
- ``tracestate``: vendor-specific extensions; not used by v1 of this SDK.

Usage at the *callee* boundary (agent A calls agent B over HTTP)::

    headers = inject()   # add to outgoing request

Usage at the *receiver* boundary (agent B handling the inbound request)::

    remote = extract(request.headers)
    if remote is not None:
        # build root span of B's trajectory using remote.trajectory_id +
        # remote.span_id as parent.

OpenTelemetry's ``TraceContextTextMapPropagator`` does the same thing.
We hand-roll for v1 to avoid pulling the OTel SDK as a hard dependency
for an otherwise self-contained module; an OTel-based propagator can be
added later for vendors who need the broader propagator chain (B3, jaeger,
etc.).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agent_capture.context.propagation import current_parent

TRACEPARENT_HEADER = "traceparent"
TRACESTATE_HEADER = "tracestate"

_TRACEPARENT_PATTERN = re.compile(
    r"^(?P<version>[0-9a-f]{2})-"
    r"(?P<trace_id>[0-9a-f]{32})-"
    r"(?P<span_id>[0-9a-f]{16})-"
    r"(?P<flags>[0-9a-f]{2})$"
)


@dataclass(frozen=True)
class RemoteParent:
    """A parent span identified by an inbound W3C Trace Context header.

    The receiver uses ``trajectory_id`` as the trace id for any spans it
    creates in this trajectory, and ``span_id`` as the parent of its root
    span.
    """

    trajectory_id: str
    span_id: str
    sampled: bool = True


def inject(*, sampled: bool = True) -> dict[str, str]:
    """Return W3C Trace Context headers for the current parent span.

    Returns an empty dict if there is no active parent (the call is
    outside any traced scope), so the caller can ``headers.update(inject())``
    unconditionally.
    """
    parent = current_parent()
    if parent is None:
        return {}
    flags = "01" if sampled else "00"
    return {
        TRACEPARENT_HEADER: f"00-{parent.trajectory_id}-{parent.span_id}-{flags}",
    }


def extract(headers: Mapping[str, str]) -> RemoteParent | None:
    """Parse a ``traceparent`` header into a :class:`RemoteParent`, or ``None``.

    ``headers`` is any mapping. Header lookup is case-insensitive.
    Malformed headers return ``None`` rather than raising — the caller
    treats the inbound request as starting a fresh trajectory.
    """
    raw = _case_insensitive_get(headers, TRACEPARENT_HEADER)
    if raw is None:
        return None
    match = _TRACEPARENT_PATTERN.match(raw.strip())
    if match is None:
        return None
    if match.group("version") == "ff":
        return None  # reserved per spec
    flags = int(match.group("flags"), 16)
    return RemoteParent(
        trajectory_id=match.group("trace_id"),
        span_id=match.group("span_id"),
        sampled=bool(flags & 0x01),
    )


def _case_insensitive_get(headers: Mapping[str, str], key: str) -> str | None:
    """HTTP headers are case-insensitive; mappings often are not."""
    if key in headers:
        return headers[key]
    target = key.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return None
