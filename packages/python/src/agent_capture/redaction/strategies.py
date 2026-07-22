"""Replacement strategies (architecture doc §8.2).

Two production strategies:

- :class:`FullRedaction` — value replaced with ``[REDACTED:<field_type>]``.
  The downstream ledger never holds the original. Right default for
  fields the customer never needs to audit a specific value of.
- :class:`HmacFingerprint` — value replaced with
  ``HMAC-SHA256(customer_key, value)``. The key is customer-managed
  (BYOK via KMS), injected into the recorder at use time; the vendor
  never persists the plaintext key. Lets an auditor later prove "the
  agent saw this specific SSN" by re-fingerprinting the candidate value
  and comparing.

Plus :class:`PassThrough` for explicit no-redact paths (e.g. fields the
customer has classified as public).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_capture.errors import RedactionError


@runtime_checkable
class RedactionStrategy(Protocol):
    """Replace one sensitive value with its redacted form.

    Implementations typically expose a ``name`` attribute for telemetry,
    but it is not part of the structural protocol — only ``redact()`` is.
    """

    def redact(self, value: str, *, field_type: str) -> str:
        """Return the replacement for ``value``.

        Args:
            value: The original sensitive string. Must not be logged.
            field_type: A short label describing what kind of field this
                is (``"ssn"``, ``"account_number"``, etc.) — embedded in
                the output for downstream visibility.
        """
        ...


@dataclass(frozen=True)
class FullRedaction:
    """Replace the value with a fixed sentinel."""

    name: str = "full"

    def redact(self, value: str, *, field_type: str) -> str:
        return f"[REDACTED:{field_type}]"


@dataclass(frozen=True)
class PassThrough:
    """No replacement — return the value verbatim.

    Use only when the policy explicitly classifies a field as
    non-sensitive. Defaulting to PassThrough is wrong; defaulting to
    FullRedaction is right.
    """

    name: str = "pass_through"

    def redact(self, value: str, *, field_type: str) -> str:
        return value


@dataclass(frozen=True)
class HmacFingerprint:
    """Replace with HMAC-SHA256(customer_key, value).

    The key is customer-managed (BYOK via KMS): the recorder runs
    vendor-side, so the key is injected into the recorder process via a
    KMS/secret reference at use time and never written to vendor code,
    config, or the durable record. The fingerprint is deterministic, so
    an auditor can later prove a span "saw" a specific candidate value by
    re-fingerprinting and comparing.

    Output format: ``[FP:<hex>:<field_type>]`` so the downstream ledger
    can tell at a glance that a value was fingerprinted (not full-redacted)
    and what field type it was.

    Args:
        key_env: Environment variable holding the HMAC key. Looked up
            lazily on each ``redact()`` call so key rotations take effect
            without restarting the agent.
        key: Inline key for tests only. Production deployments MUST use
            ``key_env`` so the key never appears in code or config files.
        truncate: Number of hex chars to keep in the fingerprint. The
            default of 32 (128 bits) is more than enough collision
            resistance for compliance lookups.
    """

    name: str = "hmac"
    key_env: str | None = None
    key: bytes | None = None
    truncate: int = 32

    def redact(self, value: str, *, field_type: str) -> str:
        key = self._resolve_key()
        digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"[FP:{digest[: self.truncate]}:{field_type}]"

    def _resolve_key(self) -> bytes:
        if self.key is not None:
            return self.key
        if self.key_env is None:
            raise RedactionError("HmacFingerprint requires either key= (tests) or key_env= (production).")
        raw = os.environ.get(self.key_env)
        if not raw:
            raise RedactionError(f"HmacFingerprint: env var {self.key_env!r} is unset or empty.")
        return raw.encode("utf-8")
