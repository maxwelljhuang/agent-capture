"""Tenant-routing exporter — one process, many tenants, per-tenant tokens.

Routes each span to a per-tenant inner exporter keyed by
``span.compliance.end_customer_id``. For a multi-tenant SaaS recorder where each
tenant has its **own** ingest token (isolation / independent revocation). If you
would rather use a single token for all tenants, mint an unscoped ingest token
instead (``ledger token create --role ingest --unscoped``) and use a plain
:class:`~agent_capture.exporter.http.HTTPExporter`.

Per-tenant pipelines are built lazily via a factory and cached. A tenant with no
exporter (factory returns ``None``) is dropped + logged (``AC414``), never
raising — the cardinal rule: the agent must always win.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.exporter.base import SpanExporter
from agent_capture.exporter.http import HTTPExporter
from agent_capture.exporter.queue import BoundedQueueExporter
from agent_capture.schema import Span


def _default_pipeline(endpoint: str, token: str, http_kwargs: Mapping[str, Any]) -> SpanExporter:
    """A per-tenant pipeline: bounded queue in front of an HTTP exporter."""
    return BoundedQueueExporter(HTTPExporter(endpoint, auth_token=token, **http_kwargs))


class TenantRoutingExporter:
    """Dispatch spans to per-tenant exporters by ``end_customer_id``.

    Args:
        factory: ``tenant_id -> SpanExporter | None``. Called once per tenant
            (result cached); ``None`` means "no token for this tenant" → the
            span is dropped + logged. Use :meth:`from_tokens` /
            :meth:`from_token_provider` for the common case.
    """

    def __init__(self, factory: Callable[[str], SpanExporter | None]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._exporters: dict[str, SpanExporter | None] = {}
        self._warned: set[str] = set()
        self.dropped_count = 0

    @classmethod
    def from_tokens(cls, endpoint: str, tokens: Mapping[str, str], **http_kwargs: Any) -> TenantRoutingExporter:
        """Static ``{tenant_id: ingest_token}`` map; each tenant gets a default pipeline."""
        token_map = dict(tokens)
        return cls(lambda t: _default_pipeline(endpoint, token_map[t], http_kwargs) if t in token_map else None)

    @classmethod
    def from_token_provider(
        cls, endpoint: str, provider: Callable[[str], str | None], **http_kwargs: Any
    ) -> TenantRoutingExporter:
        """Dynamic ``tenant_id -> token | None`` provider (e.g. onboarding-time refresh)."""

        def factory(tenant: str) -> SpanExporter | None:
            token = provider(tenant)
            return _default_pipeline(endpoint, token, http_kwargs) if token is not None else None

        return cls(factory)

    def _exporter_for(self, tenant: str) -> SpanExporter | None:
        # Key presence = "already resolved" (the value may legitimately be None).
        if tenant not in self._exporters:
            with self._lock:
                if tenant not in self._exporters:
                    try:
                        self._exporters[tenant] = self._factory(tenant)
                    except Exception as exc:
                        log_error(ErrorCode.AC415, "tenant exporter factory failed for %r: %s", tenant, exc)
                        self._exporters[tenant] = None
        return self._exporters[tenant]

    def _live(self) -> list[SpanExporter]:
        with self._lock:
            return [e for e in self._exporters.values() if e is not None]

    def export(self, span: Span) -> None:
        tenant = span.compliance.end_customer_id
        exporter = self._exporter_for(tenant)
        if exporter is None:
            self.dropped_count += 1
            if tenant not in self._warned:
                self._warned.add(tenant)
                log_error(ErrorCode.AC414, "no exporter for tenant %r; dropping its spans", tenant, exc_info=False)
            return
        try:
            exporter.export(span)
        except Exception as exc:
            log_error(ErrorCode.AC415, "inner exporter for tenant %r raised: %s", tenant, exc)

    def flush(self, timeout: float = 5.0) -> None:
        for exporter in self._live():
            flush = getattr(exporter, "flush", None)
            if flush is None:
                continue
            try:
                flush(timeout)
            except Exception as exc:
                log_error(ErrorCode.AC415, "inner exporter flush raised: %s", exc)

    def shutdown(self, timeout: float = 5.0) -> None:
        for exporter in self._live():
            try:
                exporter.shutdown(timeout)
            except Exception as exc:
                log_error(ErrorCode.AC415, "inner exporter shutdown raised: %s", exc)


__all__ = ["TenantRoutingExporter"]
