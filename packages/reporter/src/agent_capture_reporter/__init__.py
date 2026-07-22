"""agent-capture reporter — layer 3 of the compliance stack.

Turns a captured, redacted, content-hashed agent trajectory into a
compliance-officer-facing document. v1 renders the ECOA Adverse Action Notice
(Regulation B + FCRA) as HTML and PDF, plus a JSON manifest that traces every
section of the notice back to the source spans' ``content_hash``es.

Typical use::

    from datetime import UTC, datetime
    from agent_capture_reporter import load_trajectory, render_adverse_action

    trajectory = load_trajectory("decision.jsonl")
    rendered = render_adverse_action(trajectory, generated_at=datetime.now(UTC))
    Path("notice.html").write_text(rendered.html)
    Path("manifest.json").write_text(rendered.manifest.model_dump_json(indent=2))
"""

from agent_capture_reporter.common.corpus import Corpus, ReportingPeriod, load_corpus
from agent_capture_reporter.common.ledger_source import (
    LedgerClient,
    load_corpus_from_ledger,
    load_trajectory_from_ledger,
)
from agent_capture_reporter.ecoa.model import AdverseActionModel
from agent_capture_reporter.errors import (
    IncompleteInventoryError,
    IncompleteTrajectoryError,
    RenderError,
    ReporterError,
    TrajectoryLoadError,
)
from agent_capture_reporter.manifest.inventory import ModelInventoryManifest
from agent_capture_reporter.manifest.schema import NoticeManifest, SectionProvenance, TrajectoryGap
from agent_capture_reporter.report import (
    RenderedInventory,
    RenderedNotice,
    render_adverse_action,
    render_model_inventory,
)
from agent_capture_reporter.sr_11_7.model import ModelInventoryModel
from agent_capture_reporter.sr_11_7.registry import ModelGovernanceEntry, ModelGovernanceRegistry
from agent_capture_reporter.trajectory import Trajectory, load_trajectory
from agent_capture_reporter.version import __version__

__all__ = [
    "AdverseActionModel",
    "Corpus",
    "IncompleteInventoryError",
    "IncompleteTrajectoryError",
    "LedgerClient",
    "ModelGovernanceEntry",
    "ModelGovernanceRegistry",
    "ModelInventoryManifest",
    "ModelInventoryModel",
    "NoticeManifest",
    "RenderError",
    "RenderedInventory",
    "RenderedNotice",
    "ReporterError",
    "ReportingPeriod",
    "SectionProvenance",
    "Trajectory",
    "TrajectoryGap",
    "TrajectoryLoadError",
    "__version__",
    "load_corpus",
    "load_corpus_from_ledger",
    "load_trajectory",
    "load_trajectory_from_ledger",
    "render_adverse_action",
    "render_model_inventory",
]
