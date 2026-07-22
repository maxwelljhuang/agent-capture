"""ECOA Adverse Action Notice: extraction, view-model, and section registry."""

from agent_capture_reporter.ecoa.extract import ExtractionResult, extract_adverse_action
from agent_capture_reporter.ecoa.model import AdverseActionModel

__all__ = [
    "AdverseActionModel",
    "ExtractionResult",
    "extract_adverse_action",
]
