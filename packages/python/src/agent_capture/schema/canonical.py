"""Canonical span serialization for hashing.

The provenance hash chain depends on Python and TypeScript producing
byte-identical bytes for a given logical span. This module owns the
canonical form. Any change here is a schema-breaking change and must bump
:data:`agent_capture.schema.provenance.SCHEMA_VERSION`.

Rules of canonicalization (mirror these in the TypeScript implementation):

1. Object keys are sorted lexicographically (UTF-8 codepoint order).
2. No insignificant whitespace. Separators are ``","`` and ``":"``.
3. UTF-8 encoding of the resulting JSON string is the byte input to SHA-256.
4. ``datetime`` values are rendered as RFC 3339 / ISO 8601 strings in UTC,
   nanosecond precision when available, ``Z`` suffix (no offset notation).
5. The ``provenance`` object is excluded from the hash input (a span cannot
   hash over its own hash).
6. ``None``/``null`` values are preserved (not stripped). The presence vs.
   absence of a key is significant.
7. Floats are emitted with Python's ``repr`` semantics ported to JSON:
   shortest round-trip decimal. NaN/Infinity are forbidden.

The output of :func:`canonical_bytes` is what gets hashed; the output of
:func:`canonical_json` is the same bytes decoded to ``str`` for debugging.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


def _normalize(value: Any) -> Any:
    """Recursively convert a value into JSON-canonicalizable primitives.

    Pydantic models are dumped via ``model_dump(mode='json')`` which already
    handles enums, datetimes, etc. — but we re-walk the result to guarantee
    every nested datetime/float is normalized consistently regardless of
    Pydantic's exact mode behavior.
    """
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("Canonical form forbids NaN and Infinity.")
        return value
    return value


def _format_datetime(dt: datetime) -> str:
    """Render a datetime as a canonical RFC 3339 UTC string."""
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    # microsecond precision is the most Python natively offers; pad to a fixed
    # width so the serialized form is deterministic.
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    micros = dt.microsecond
    return f"{base}.{micros:06d}Z"


def canonical_bytes(span_like: BaseModel | dict[str, Any], *, exclude_provenance: bool = True) -> bytes:
    """Return the canonical UTF-8 byte string for hashing.

    Args:
        span_like: A :class:`~agent_capture.schema.span.Span`, any Pydantic
            model, or a plain dict produced from one.
        exclude_provenance: When ``True`` (the default), the ``provenance``
            key is removed before serialization. This is the correct setting
            when computing a span's own ``content_hash``. Pass ``False`` for
            external verification once a chain already includes a hash.

    Returns:
        UTF-8 bytes ready to feed into ``hashlib.sha256(...)``.
    """
    payload = span_like.model_dump(mode="json") if isinstance(span_like, BaseModel) else dict(span_like)

    if exclude_provenance and isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k != "provenance"}

    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(span_like: BaseModel | dict[str, Any], *, exclude_provenance: bool = True) -> str:
    """String form of :func:`canonical_bytes`. For debugging / golden fixtures."""
    return canonical_bytes(span_like, exclude_provenance=exclude_provenance).decode("utf-8")


def content_hash(span_like: BaseModel | dict[str, Any]) -> str:
    """Compute the SHA-256 ``content_hash`` for a span.

    Excludes the ``provenance`` key from the input — see module docstring.
    """
    return hashlib.sha256(canonical_bytes(span_like, exclude_provenance=True)).hexdigest()
