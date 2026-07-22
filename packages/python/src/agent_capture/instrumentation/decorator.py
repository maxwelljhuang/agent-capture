"""``@traced`` — decorator and context manager for manual instrumentation.

Two usage patterns from the architecture doc §5.3::

    @traced(type=SpanType.RETRIEVAL)
    def fetch_credit_report(applicant_id: str) -> dict: ...

    with traced(type=SpanType.PLANNER_STEP, name="decide"):
        ...

The single ``traced(...)`` call returns an object that is both a decorator
(``__call__``) and a context manager (``__enter__``/``__exit__``).

Discipline (non-negotiable rule #1, CLAUDE.md): the SDK's own failures
must never propagate to the host. The decorator catches *its own*
exceptions (open/close failures, missing builder) and logs them to safelog.
Exceptions raised by the wrapped function or block are captured into a
:class:`ErrorInfo`, recorded on the span, and re-raised — the host's
control flow is preserved exactly.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import Callable
from types import TracebackType
from typing import Any, TypeVar

from agent_capture._internal.runtime import default_builder
from agent_capture._internal.safelog import ErrorCode, log_error, safelog
from agent_capture.context.propagation import span_scope
from agent_capture.enforcement.gate import (
    EnforcementBlocked,
    Verdict,
    evaluate_gate,
    evaluate_gate_async,
    is_blocking,
)
from agent_capture.schema import ComplianceMetadata, ErrorInfo, Span, SpanStatus, SpanType
from agent_capture.schema.types import (
    HumanApprovalAttributes,
    ModelCallAttributes,
    PlannerStepAttributes,
    PolicyCheckAttributes,
    RetrievalAttributes,
    SideEffectAttributes,
    SubAgentInvocationAttributes,
    ToolCallAttributes,
    TypedAttributes,
)
from agent_capture.span.builder import OpenSpan, SpanBuilder

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_ATTRS_BY_TYPE: dict[SpanType, Callable[[str], TypedAttributes]] = {
    SpanType.MODEL_CALL: lambda name: ModelCallAttributes(model_name=name, provider="unknown"),
    SpanType.TOOL_CALL: lambda name: ToolCallAttributes(tool_name=name),
    SpanType.RETRIEVAL: lambda name: RetrievalAttributes(source_identifier=name),
    SpanType.PLANNER_STEP: lambda name: PlannerStepAttributes(),
    SpanType.SUB_AGENT_INVOCATION: lambda name: SubAgentInvocationAttributes(sub_agent_identity=name),
    SpanType.HUMAN_APPROVAL: lambda name: HumanApprovalAttributes(
        approver_identity="unknown",
        approver_role="unknown",
        decision="approved",
        decision_timestamp="1970-01-01T00:00:00Z",
        artifact_reviewed=name,
    ),
    SpanType.SIDE_EFFECT: lambda name: SideEffectAttributes(
        action_type=name,
        target_system="unknown",
        success=True,
    ),
    SpanType.POLICY_CHECK: lambda name: PolicyCheckAttributes(
        policy_name=name,
        policy_version="unknown",
        result="not_applicable",
    ),
}


class traced:
    """Manual instrumentation primitive.

    Use as a decorator::

        @traced(type=SpanType.RETRIEVAL, name="fetch_credit_report")
        def fetch_credit_report(applicant_id: str) -> dict: ...

    or as a context manager::

        with traced(type=SpanType.PLANNER_STEP, name="decide"):
            ...

    Args:
        type: The span type. Either a :class:`SpanType` or its string value.
        name: Human-readable label. Defaults to the wrapped function's name
            when used as a decorator, or to ``type.value`` when used as a
            context manager.
        attributes: Pre-built per-type attributes. If omitted, a minimal
            placeholder is created and the caller is expected to fill in
            real attributes after the call via the framework adapters or
            SDK wrappers (Week 3).
        compliance: Compliance metadata override. Falls back to the
            builder's configured default.
        builder: Override builder, primarily for testing. Defaults to the
            process-wide builder set by :func:`agent_capture.configure`.
    """

    def __init__(
        self,
        *,
        type: SpanType | str,
        name: str | None = None,
        attributes: TypedAttributes | None = None,
        compliance: ComplianceMetadata | None = None,
        inputs: Any | None = None,
        builder: SpanBuilder | None = None,
    ) -> None:
        self._type: SpanType = SpanType(type) if isinstance(type, str) else type
        self._explicit_name = name
        self._attributes = attributes
        self._compliance = compliance
        self._inputs = inputs
        self._builder_override = builder

        # Populated only when used as a context manager.
        self._cm_open_span: OpenSpan | None = None
        self._cm_scope_ctx: Any = None
        self._cm_outputs: Any = None
        self._cm_outputs_set: bool = False

    def set_outputs(self, value: Any) -> None:
        """Record outputs for the close. Only meaningful while inside a ``with`` block.

        Use when ``traced`` is acting as a context manager and the body
        computes a result the span should record (matches what the
        decorator form captures automatically from a function's return).
        """
        self._cm_outputs = value
        self._cm_outputs_set = True

    # ---- decorator surface ------------------------------------------------

    def __call__(self, func: F) -> F:
        if inspect.iscoroutinefunction(func):
            return self._wrap_async(func)  # type: ignore[return-value]
        return self._wrap_sync(func)  # type: ignore[return-value]

    def _wrap_sync(self, func: Callable[..., Any]) -> Callable[..., Any]:
        type_ = self._type
        explicit_name = self._explicit_name
        attributes = self._attributes
        compliance = self._compliance
        builder_override = self._builder_override

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            builder = builder_override or default_builder()
            if builder is None:
                safelog().debug("traced(): no builder configured; passing through")
                return func(*args, **kwargs)
            try:
                open_span = builder.open(
                    name=explicit_name or func.__name__,
                    type=type_,
                    attributes=attributes
                    if attributes is not None
                    else _DEFAULT_ATTRS_BY_TYPE[type_](explicit_name or func.__name__),
                    compliance=compliance,
                    inputs=_safe_call_inputs(func, args, kwargs),
                )
            except Exception as exc:
                log_error(ErrorCode.AC102, "traced(): open failed: %s", exc)
                return func(*args, **kwargs)

            with span_scope(open_span):
                verdict = evaluate_gate(open_span)
                if is_blocking(verdict):
                    raise _blocked(builder, open_span, verdict)
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:
                    _close_with_error(builder, open_span, exc)
                    raise
                else:
                    builder.close(open_span, outputs=_safe_outputs(result))
                    return result

        return wrapper

    def _wrap_async(self, func: Callable[..., Any]) -> Callable[..., Any]:
        type_ = self._type
        explicit_name = self._explicit_name
        attributes = self._attributes
        compliance = self._compliance
        builder_override = self._builder_override

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            builder = builder_override or default_builder()
            if builder is None:
                safelog().debug("traced(): no builder configured; passing through")
                return await func(*args, **kwargs)
            try:
                open_span = builder.open(
                    name=explicit_name or func.__name__,
                    type=type_,
                    attributes=attributes
                    if attributes is not None
                    else _DEFAULT_ATTRS_BY_TYPE[type_](explicit_name or func.__name__),
                    compliance=compliance,
                    inputs=_safe_call_inputs(func, args, kwargs),
                )
            except Exception as exc:
                log_error(ErrorCode.AC102, "traced(): open failed: %s", exc)
                return await func(*args, **kwargs)

            with span_scope(open_span):
                verdict = await evaluate_gate_async(open_span)
                if is_blocking(verdict):
                    raise _blocked(builder, open_span, verdict)
                try:
                    result = await func(*args, **kwargs)
                except BaseException as exc:
                    _close_with_error(builder, open_span, exc)
                    raise
                else:
                    builder.close(open_span, outputs=_safe_outputs(result))
                    return result

        return wrapper

    # ---- context-manager surface -----------------------------------------

    def __enter__(self) -> OpenSpan | None:
        builder = self._builder_override or default_builder()
        if builder is None:
            safelog().debug("traced(): no builder configured; entering no-op scope")
            return None
        try:
            open_span = builder.open(
                name=self._explicit_name or self._type.value,
                type=self._type,
                attributes=self._attributes
                if self._attributes is not None
                else _DEFAULT_ATTRS_BY_TYPE[self._type](self._explicit_name or self._type.value),
                compliance=self._compliance,
                inputs=self._inputs,
            )
        except Exception as exc:
            log_error(ErrorCode.AC102, "traced.__enter__ open failed: %s", exc)
            return None
        self._cm_open_span = open_span
        self._cm_scope_ctx = span_scope(open_span)
        self._cm_scope_ctx.__enter__()

        verdict = evaluate_gate(open_span)
        if is_blocking(verdict):
            # __exit__ is NOT called when __enter__ raises, so unwind the scope
            # and close the span here before raising into the host.
            blocked = EnforcementBlocked(verdict)  # type: ignore[arg-type]  # is_blocking ⇒ not None
            try:
                self._cm_scope_ctx.__exit__(type(blocked), blocked, blocked.__traceback__)
            except Exception as exit_exc:
                log_error(ErrorCode.AC104, "traced.__enter__ block unwind failed: %s", exit_exc)
            self._cm_scope_ctx = None  # neutralize any stray __exit__
            _close_with_error(builder, open_span, blocked)
            raise blocked
        return open_span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._cm_scope_ctx is None:
            return  # no-op scope; nothing to close
        try:
            self._cm_scope_ctx.__exit__(exc_type, exc, tb)
        except Exception as exit_exc:
            log_error(ErrorCode.AC104, "traced.__exit__ span_scope failed: %s", exit_exc)
        if self._cm_open_span is None:
            return
        builder = self._builder_override or default_builder()
        if builder is None:
            return
        if exc is not None:
            _close_with_error(builder, self._cm_open_span, exc)
        else:
            close_kwargs: dict[str, Any] = {}
            if self._cm_outputs_set:
                close_kwargs["outputs"] = _safe_outputs(self._cm_outputs)
            builder.close(self._cm_open_span, **close_kwargs)
        # Implicit None return — never suppress the host's exception.


def _blocked(builder: SpanBuilder, open_span: OpenSpan, verdict: Verdict | None) -> EnforcementBlocked:
    """Close a gated span as enforcement-blocked and return the exception to raise.

    The gated span closes with ``status=error`` carrying the verdict, and the
    host sees :class:`EnforcementBlocked` — the one deliberate case where the
    agent does *not* win, for an irreversible action a rule refused. For a
    ``side_effect`` the recorded ``success`` flips to ``False``: the effect did
    not occur because we stopped it.
    """
    assert verdict is not None  # guarded by is_blocking() at every call site
    attrs = open_span.attributes
    if getattr(attrs, "success", None) is True:
        with contextlib.suppress(Exception):
            open_span.attributes = attrs.model_copy(update={"success": False})
    exc = EnforcementBlocked(verdict)
    _close_with_error(builder, open_span, exc)
    return exc


def _close_with_error(builder: SpanBuilder, open_span: OpenSpan, exc: BaseException) -> Span | None:
    """Close a span carrying an error, never re-raising into the host."""
    try:
        return builder.close(
            open_span,
            status=SpanStatus.ERROR,
            error=ErrorInfo(
                error_type=f"{exc.__class__.__module__}.{exc.__class__.__qualname__}",
                message=str(exc),
            ),
        )
    except Exception as close_exc:
        log_error(ErrorCode.AC103, "traced(): close-with-error failed: %s", close_exc)
        return None


def _safe_call_inputs(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort capture of call args. Never raises."""
    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        return {k: _safe_repr(v) for k, v in bound.arguments.items()}
    except Exception:
        return None


def _safe_outputs(result: Any) -> Any:
    """Coerce a return value to something JSON-serializable. Never raises."""
    return _safe_repr(result)


def _safe_repr(value: Any) -> Any:
    """Return ``value`` unchanged if it round-trips through Pydantic's JSON,
    otherwise its ``repr()``. Cheap, never raises.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_repr(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_repr(v) for k, v in value.items()}
    try:
        return repr(value)
    except Exception:
        return "<unrepresentable>"
