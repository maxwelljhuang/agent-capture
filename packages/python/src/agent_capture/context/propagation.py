"""The "current parent span" pointer.

Uses :class:`contextvars.ContextVar`, which automatically propagates across
``asyncio.create_task`` boundaries. For ``concurrent.futures.ThreadPoolExecutor``
or raw ``threading.Thread`` work, the caller must hand the worker a copied
context via :func:`contextvars.copy_context` — see
:func:`run_in_context` for the helper.

The pointer stores an :class:`~agent_capture.span.builder.OpenSpan` (the
in-progress mutable state, not a finalized :class:`~agent_capture.schema.span.Span`).
The builder needs the live parent so it can register children for
``parent_content_hash`` stamping when the parent closes.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from agent_capture.span.builder import OpenSpan

_current_parent: ContextVar[OpenSpan | None] = ContextVar(
    "agent_capture_current_parent",
    default=None,
)

# Set by a framework adapter when it has already opened a model_call span for
# the current scope. SDK wrappers (anthropic, openai) read this and skip their
# own span emission to avoid double-counting. Architecture doc §5.1 + §5.2:
# the framework owns the model_call span when both layers see the same call.
_suppress_model_call: ContextVar[bool] = ContextVar(
    "agent_capture_suppress_model_call",
    default=False,
)

T = TypeVar("T")


def current_parent() -> OpenSpan | None:
    """Return the currently-active parent OpenSpan, or ``None`` at the root."""
    return _current_parent.get()


@contextmanager
def span_scope(span: OpenSpan) -> Iterator[OpenSpan]:
    """Install ``span`` as the current parent for the duration of the block.

    Restores the previous parent on exit, whether normally or via exception.
    Safe to nest. Automatically propagates to ``asyncio.create_task`` work
    started inside the block.
    """
    token = _current_parent.set(span)
    try:
        yield span
    finally:
        _current_parent.reset(token)


def model_call_suppressed() -> bool:
    """Whether SDK wrappers should skip emitting a model_call span.

    Framework adapters (LangGraph etc.) set this while they own the
    model_call span to prevent the SDK wrapper from double-counting.
    """
    return _suppress_model_call.get()


@contextmanager
def suppress_model_call_capture() -> Iterator[None]:
    """Context manager: SDK wrappers inside this block are no-ops for model_call.

    Used by framework adapters that emit their own model_call spans on
    ``on_llm_start``/``on_llm_end`` lifecycle hooks. Restores the previous
    flag on exit.
    """
    token = _suppress_model_call.set(True)
    try:
        yield
    finally:
        _suppress_model_call.reset(token)


def bind_context(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap ``fn`` so that calling the wrapper runs ``fn`` under the contextvars
    snapshot taken at the time of *this* call.

    Use when handing work to a thread pool or raw ``threading.Thread``;
    contextvars do not propagate across thread boundaries automatically.

    Example::

        with span_scope(parent):
            executor.submit(bind_context(do_work), arg)  # parent visible in worker

    ``asyncio.create_task`` already propagates automatically and does not
    need this helper.
    """
    ctx = copy_context()

    @functools.wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> T:
        return ctx.run(fn, *args, **kwargs)

    return wrapped
