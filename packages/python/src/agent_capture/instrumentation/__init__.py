"""Instrumentation layer — the vendor-facing integration surface.

Three tiers, in priority order (matching architecture doc §5):

1. **Framework adapters** (``adapters/``) — register a callback handler with
   the agent framework (LangGraph first). Vendor integration cost:
   essentially zero. Week 3.
2. **SDK wrappers** (``sdk_wrappers/``) — wrap model-provider clients so
   ``client = wrap(Anthropic())`` is the only code change required to
   capture every ``messages.create`` call. Week 3.
3. **Manual decorator** (:mod:`.decorator`) — ``@traced(type=...)`` for
   arbitrary functions.

The MCP interceptor described in §5.4 is deferred past v1, but the span
schema already accommodates it.
"""

from agent_capture.instrumentation.decorator import traced

__all__ = ["traced"]
