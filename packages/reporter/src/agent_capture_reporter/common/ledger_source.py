"""Read trajectories from the vendor-cloud ledger's HTTP API.

This closes the production seam: instead of loading trajectories from local
JSONL files, the reporter pulls them straight from the ledger (layer 2) that the
recorder ships to. The ledger's read API already exposes everything needed, so
this is a pure client — no ledger changes.

Endpoints used (see ``agent_capture_ledger.api.routes.trajectories``):
- ``GET /trajectories/{id}/spans`` → ``{"trajectory_id":…, "spans":[<span json>]}``
- ``GET /trajectories?from=&to=&cursor=&limit=`` → ``{"items":[…], "next_cursor":…}``

Auth is a ``reader`` bearer token; the ledger auto-scopes a non-admin reader to
its own ``end_customer_id``, so a per-customer token yields a single-tenant
corpus by construction. The reporter still recomputes ``content_hash`` locally
via :meth:`Trajectory.from_spans` — it does not *trust* the ledger.
"""

from __future__ import annotations

from datetime import datetime
from types import TracebackType
from typing import Any

import httpx
from agent_capture.schema import Span

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod
from agent_capture_reporter.errors import TrajectoryLoadError
from agent_capture_reporter.trajectory import Trajectory

_PAGE_LIMIT = 500


class LedgerClient:
    """Minimal read client for the ledger's trajectory endpoints.

    Pass ``base_url`` + ``token`` for production (a private ``httpx.Client`` is
    built and owned). Tests inject ``client`` — e.g. a Starlette ``TestClient``,
    which is an ``httpx.Client`` subclass over the in-process app — and the
    bearer is still sent per request.
    """

    def __init__(
        self,
        base_url: str = "",
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(base_url=base_url, timeout=timeout)

    def __enter__(self) -> LedgerClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying client if this instance created it."""
        if self._owns_client:
            self._client.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            resp = self._client.get(path, params=params, headers=self._headers())
        except httpx.HTTPError as exc:  # network/transport failure
            raise TrajectoryLoadError(f"ledger request to {path} failed: {exc}") from exc
        if resp.status_code != 200:
            raise TrajectoryLoadError(f"ledger GET {path} returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def get_trajectory_spans(self, trajectory_id: str) -> list[Span]:
        """Fetch and rehydrate every span of one trajectory."""
        data = self._get(f"/trajectories/{trajectory_id}/spans")
        raw_spans = data.get("spans", [])
        if not raw_spans:
            raise TrajectoryLoadError(f"ledger returned no spans for trajectory {trajectory_id}")
        return [Span.model_validate(s) for s in raw_spans]

    def list_trajectory_ids(self, *, frm: datetime, to: datetime) -> list[str]:
        """List trajectory ids whose spans fall in ``[frm, to]`` (cursor-paged)."""
        ids: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"from": frm.isoformat(), "to": to.isoformat(), "limit": _PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/trajectories", params=params)
            ids.extend(item["trajectory_id"] for item in data.get("items", []))
            cursor = data.get("next_cursor")
            if not cursor:
                return ids


def load_trajectory_from_ledger(
    client: LedgerClient,
    trajectory_id: str,
    *,
    verify_hashes: bool = True,
) -> Trajectory:
    """Load one trajectory from the ledger, validating it like the file loader.

    Raises:
        TrajectoryLoadError: on transport/HTTP failure, an unknown trajectory, or
            a structural/hash invariant violation (via :meth:`Trajectory.from_spans`).
    """
    spans = client.get_trajectory_spans(trajectory_id)
    return Trajectory.from_spans(spans, verify_hashes=verify_hashes)


def load_corpus_from_ledger(
    client: LedgerClient,
    period: ReportingPeriod,
    *,
    verify_hashes: bool = True,
) -> Corpus:
    """Load every trajectory in the reporting period from the ledger as a Corpus.

    An empty result returns an empty :class:`Corpus`; the inventory extractor
    raises the appropriate domain error (no in-period model usage) rather than
    this loader guessing intent.
    """
    ids = client.list_trajectory_ids(frm=period.start, to=period.end)
    trajectories = tuple(
        Trajectory.from_spans(client.get_trajectory_spans(tid), verify_hashes=verify_hashes) for tid in ids
    )
    return Corpus(trajectories=trajectories)
