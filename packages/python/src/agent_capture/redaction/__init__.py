"""In-process redaction filter.

The filter runs synchronously inside :meth:`SpanBuilder.close` *before*
``content_hash`` is computed. Sensitive values are replaced according to
the end customer's policy and the bytes that get hashed and shipped are
the post-redaction bytes — the downstream ledger never holds the original.

Two layers (architecture doc §8):

- **Schema-aware** (:mod:`.schema_aware`) — dict-field-name rules; exact,
  no false positives. Sub-trees inherit the matched strategy.
- **Pattern-based** (:mod:`.pattern`) — :mod:`.patterns_finance` regex
  pack (US SSN, ABA routing w/ checksum, account, MICR, DOB). Presidio
  integration available behind the ``[redaction]`` extra.

Two replacement strategies (:mod:`.strategies`):

- ``FullRedaction`` — value replaced with ``[REDACTED:field_type]``.
- ``HmacFingerprint`` — HMAC-SHA256 with a customer-managed key (BYOK
  via KMS). Lets auditors prove "the agent saw this specific value"
  without storing it.

Policy ownership: the end customer supplies the YAML; the vendor never
edits it. See :mod:`.policy`.
"""

from agent_capture.redaction.filter import RedactionFilter
from agent_capture.redaction.policy import (
    Policy,
    load_policy,
    parse_policy,
    pass_through_policy,
)
from agent_capture.redaction.strategies import (
    FullRedaction,
    HmacFingerprint,
    PassThrough,
    RedactionStrategy,
)

__all__ = [
    "FullRedaction",
    "HmacFingerprint",
    "PassThrough",
    "Policy",
    "RedactionFilter",
    "RedactionStrategy",
    "load_policy",
    "parse_policy",
    "pass_through_policy",
]
