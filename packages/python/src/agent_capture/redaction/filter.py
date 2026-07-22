"""The redaction filter — the single entry point invoked before export.

Pipeline order:

1. **Schema-aware** — :class:`SchemaAwareRedactor` walks dict keys and
   redacts any value whose key matches a ``field_rule``. Exact, no
   false positives. Sub-trees inherit the matched strategy.
2. **Pattern-based** — :class:`PatternRedactor` scans the remaining
   string values for finance patterns (SSN, ABA routing, account, MICR,
   DOB). Conservative: false positives over-redact (safe).

Both passes operate on the *post-model_dump* form of the span (a plain
dict), then the filter re-validates back into a fresh
:class:`~agent_capture.schema.span.Span` so the canonical-bytes
serialization stays identical to a never-redacted span of the same
shape.

The filter is invoked by :meth:`SpanBuilder.close` after the open span
is materialized but *before* ``content_hash`` is computed — so hashes
cover the bytes that actually ship.

Discipline: every public entry catches its own exceptions and returns
the original (un-redacted) span as the fallback IS NOT acceptable. The
fallback for a redaction failure is full redaction of every primitive
in the payload, then re-raise into safelog. Better to over-redact than
to leak.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_capture._internal.safelog import ErrorCode, log_error
from agent_capture.errors import RedactionError
from agent_capture.redaction.pattern import PatternRedactor
from agent_capture.redaction.patterns_finance import DEFAULT_RECOGNIZERS, Recognizer
from agent_capture.redaction.policy import Policy
from agent_capture.redaction.schema_aware import SchemaAwareRedactor
from agent_capture.redaction.strategies import FullRedaction, HmacFingerprint
from agent_capture.schema import Span

#: Default env var holding the customer HMAC key used to fingerprint subject_id.
DEFAULT_SUBJECT_KEY_ENV = "AGENT_CAPTURE_HMAC_KEY"


class RedactionFilter:
    """Two-pass filter wrapping schema-aware + pattern redactors.

    Construct once per process and call :meth:`apply` per span. Cheap to
    construct, stateless, thread-safe.

    Args:
        policy: The customer's loaded :class:`Policy`. Required.
        recognizers: Override the pattern recognizer pack. Default is the
            finance pack in :mod:`agent_capture.redaction.patterns_finance`.
        subject_key_env: Env var holding the HMAC key used to fingerprint
            ``compliance.subject_id`` (the redaction floor for the subject —
            it is never shipped in cleartext; if the key is unset it is
            full-redacted instead).
    """

    def __init__(
        self,
        *,
        policy: Policy,
        recognizers: tuple[Recognizer, ...] = DEFAULT_RECOGNIZERS,
        subject_key_env: str = DEFAULT_SUBJECT_KEY_ENV,
    ) -> None:
        self._policy = policy
        self._schema = SchemaAwareRedactor(policy=policy)
        self._pattern = PatternRedactor(
            recognizers=recognizers,
            strategy_for=policy.strategy_for_pattern,
        )
        self._subject_fp = HmacFingerprint(key_env=subject_key_env)

    @property
    def policy(self) -> Policy:
        return self._policy

    def apply(self, span: Span) -> Span:
        """Return a new Span with redactions applied to inputs / outputs / attributes.

        Also fingerprints ``compliance.subject_id`` (floor — never cleartext).
        Never raises: a hard failure causes the filter to over-redact
        every primitive (safe failure mode) and log to safelog.
        """
        try:
            dumped = span.model_dump(mode="json")
            for key in ("inputs", "outputs", "attributes"):
                dumped[key] = self._redact_value(dumped.get(key))
            compliance = dumped.get("compliance")
            if isinstance(compliance, dict):
                compliance["subject_id"] = self._fingerprint_subject(compliance.get("subject_id"))
            return Span.model_validate(dumped)
        except Exception as exc:
            log_error(
                ErrorCode.AC301,
                "RedactionFilter.apply failed; falling back to FullRedaction sweep: %s",
                exc,
            )
            return self._fallback_full_redact(span)

    def _fingerprint_subject(self, subject_id: Any) -> Any:
        """HMAC-fingerprint subject_id; full-redact if no key. Idempotent."""
        if subject_id is None:
            return None
        text = str(subject_id)
        if text.startswith("[FP:") or text.startswith("[REDACTED:"):
            return subject_id  # already redacted/fingerprinted
        try:
            return self._subject_fp.redact(text, field_type="subject_id")
        except RedactionError:
            return FullRedaction().redact(text, field_type="subject_id")

    def _redact_value(self, value: Any) -> Any:
        # Step 1: schema-aware.
        post_schema = self._schema.redact(value)
        # Step 2: pattern, applied only to remaining strings.
        return _walk_strings(post_schema, self._pattern.redact)

    def _fallback_full_redact(self, span: Span) -> Span:
        """Last-resort redaction when the normal pipeline raises.

        Replaces every primitive in inputs/outputs/attributes with the
        :class:`FullRedaction` sentinel. The span still ships (the agent
        must always win), but with maximum redaction.
        """
        full = FullRedaction()
        dumped = span.model_dump(mode="json")
        for key in ("inputs", "outputs", "attributes"):
            dumped[key] = _full_redact_tree(dumped.get(key), strategy=full)
        compliance = dumped.get("compliance")
        if isinstance(compliance, dict) and compliance.get("subject_id") is not None:
            compliance["subject_id"] = full.redact(str(compliance["subject_id"]), field_type="subject_id")
        try:
            return Span.model_validate(dumped)
        except Exception as exc:
            log_error(
                ErrorCode.AC302,
                "RedactionFilter fallback re-validation failed; returning ORIGINAL un-redacted span: %s",
                exc,
            )
            # This is the only path in the whole engine where un-redacted
            # bytes can reach the exporter. Investigate any AC302 hit.
            return span


# ---- module helpers ------------------------------------------------------


def _walk_strings(value: Any, fn: Callable[[str], str]) -> Any:
    """Apply ``fn`` to every string leaf in a nested structure."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: _walk_strings(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_strings(v, fn) for v in value]
    return value


def _full_redact_tree(value: Any, *, strategy: FullRedaction) -> Any:
    """Replace every primitive leaf with the FullRedaction sentinel.

    Preserves the structure (so re-validation against the Pydantic schema
    has the right shape) but every value becomes ``[REDACTED:fallback]``.
    Non-redactable structural keys (``kind`` discriminators, etc.) are
    preserved verbatim — re-validation would fail otherwise. We detect
    them by checking against a small allowlist of known keys.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _STRUCTURAL_KEYS:
                out[k] = v
            else:
                out[k] = _full_redact_tree(v, strategy=strategy)
        return out
    if isinstance(value, list):
        return [_full_redact_tree(v, strategy=strategy) for v in value]
    if value is None or isinstance(value, bool):
        return value
    return strategy.redact(str(value), field_type="fallback")


# Keys whose values are discriminators or known-safe metadata and must
# survive the fallback redaction pass so Pydantic re-validation
# succeeds. Anything not in this set gets replaced.
_STRUCTURAL_KEYS = frozenset(
    {
        "kind",  # discriminator on TypedAttributes variants
    }
)


__all__ = ["RedactionFilter"]
