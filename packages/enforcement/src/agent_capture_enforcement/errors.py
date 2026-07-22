"""Enforcement engine errors.

Unlike the recorder (whose failures must never reach the host), the
enforcement engine runs off the agent's hot path and *may* raise. A
malformed rule file is fail-loud at load time — the engine must start with a
valid ruleset or not at all.
"""

from __future__ import annotations


class EnforcementError(Exception):
    """Base class for enforcement engine errors."""


class RuleLoadError(EnforcementError):
    """A rule file was missing, malformed, or referenced an unknown evaluator."""


class UnknownEvaluatorError(EnforcementError):
    """A rule referenced an evaluator name that is not registered."""
