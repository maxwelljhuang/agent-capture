"""agent-capture-enforcement — compliance enforcement engine (layer 5).

Evaluates bank-authored rules against an agent's trajectory. The two gated span
types (``side_effect``, ``human_approval``) can be acted on; everything else is
advisory. The recorder-side gate contract this engine implements lives in
``agent_capture.enforcement``.

See ``docs/enforcement-plan.md`` for the design.
"""

from agent_capture_enforcement.advisory import (
    advisory_observe,
    evaluate_rules,
    run_advisory,
)
from agent_capture_enforcement.client import EnforcementClient
from agent_capture_enforcement.config import Settings, get_settings, set_settings
from agent_capture_enforcement.decision import Decision, DecisionResult, decide
from agent_capture_enforcement.errors import (
    EnforcementError,
    RuleLoadError,
    UnknownEvaluatorError,
)
from agent_capture_enforcement.evaluator import (
    EvalOutcome,
    known_evaluators,
    register_evaluator,
)
from agent_capture_enforcement.rules import (
    EnforcementRule,
    EnforcementRuleSet,
    load_rules,
    parse_rules,
)
from agent_capture_enforcement.verdict import EnforcementVerdict

__all__ = [
    "Decision",
    "DecisionResult",
    "EnforcementClient",
    "EnforcementError",
    "EnforcementRule",
    "EnforcementRuleSet",
    "EnforcementVerdict",
    "EvalOutcome",
    "RuleLoadError",
    "Settings",
    "UnknownEvaluatorError",
    "advisory_observe",
    "decide",
    "evaluate_rules",
    "get_settings",
    "known_evaluators",
    "load_rules",
    "parse_rules",
    "register_evaluator",
    "run_advisory",
    "set_settings",
]

__version__ = "0.1.0"
