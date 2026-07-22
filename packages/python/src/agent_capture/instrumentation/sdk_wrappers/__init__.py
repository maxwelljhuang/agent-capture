"""SDK wrappers — patch model-provider clients to emit model_call spans.

The integration target is one-line::

    from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap
    client = wrap(Anthropic())

After that, every ``client.messages.create(...)`` produces a ``model_call``
span via the span builder. Wrappers preserve the underlying client's full
API surface; we only intercept the call boundaries.

Bedrock and Vertex follow as customer demand requires. Wrappers always
honor :func:`agent_capture.context.model_call_suppressed` so a framework
adapter (LangGraph etc.) can take ownership of the ``model_call`` span
without the SDK wrapper double-counting.
"""

from agent_capture.instrumentation.sdk_wrappers.anthropic import wrap as wrap_anthropic
from agent_capture.instrumentation.sdk_wrappers.openai import wrap as wrap_openai

__all__ = ["wrap_anthropic", "wrap_openai"]
