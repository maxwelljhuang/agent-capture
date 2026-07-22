"""Shared building blocks for the reporter's renderers.

Lifted here once a second renderer (SR 11-7) made the duplication real:
manifest base fields and gap/citation types (:mod:`manifest_base`), hash-chain
verification (:mod:`provenance`), and corpus loading (:mod:`corpus`). Anything
renderer-specific — extraction, view-models, templates, section registries —
deliberately stays in ``ecoa/`` and ``sr_11_7/``.
"""

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod, load_corpus
from agent_capture_reporter.common.ledger_source import (
    LedgerClient,
    load_corpus_from_ledger,
    load_trajectory_from_ledger,
)
from agent_capture_reporter.common.manifest_base import (
    MANIFEST_SCHEMA_VERSION,
    Citation,
    GapSeverity,
    ManifestBase,
    ReportGap,
)
from agent_capture_reporter.common.provenance import GapLog, verify_hash_chain

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "Citation",
    "Corpus",
    "GapLog",
    "GapSeverity",
    "LedgerClient",
    "ManifestBase",
    "ReportGap",
    "ReportingPeriod",
    "load_corpus",
    "load_corpus_from_ledger",
    "load_trajectory_from_ledger",
    "verify_hash_chain",
]
