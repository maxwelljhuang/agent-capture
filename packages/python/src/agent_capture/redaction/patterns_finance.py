"""Finance-specific pattern recognizers.

Each recognizer detects one kind of sensitive value in free text. The
recognizer returns the list of matches it found; the filter pipeline
decides how to replace them per the customer's policy.

Coverage (architecture doc §8.1):

- US SSN — ``123-45-6789`` and ``123456789``
- ABA routing number — 9 digits passing the ABA mod-10 checksum
- US bank account — heuristic for 8-17 digit sequences in account context
- MICR line — magnetic ink character recognition line on a US check
- Date of birth — common written forms (best-effort)

Recognizers are intentionally conservative — false positives over-redact
(safe), false negatives miss real PII (unsafe). When in doubt the
recognizer fires.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Match:
    """A single hit from a recognizer."""

    start: int
    end: int
    value: str
    recognizer: str


@dataclass(frozen=True)
class Recognizer:
    """A named regex (or callable) detector with a field-type label.

    ``field_type`` is the label embedded in the redaction output (e.g.
    ``[REDACTED:ssn]``) and the key the policy looks up to find which
    strategy to apply.
    """

    name: str
    field_type: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None

    def find_all(self, text: str) -> list[Match]:
        out: list[Match] = []
        for m in self.pattern.finditer(text):
            captured = m.group(0)
            if self.validator is not None and not self.validator(captured):
                continue
            out.append(Match(start=m.start(), end=m.end(), value=captured, recognizer=self.name))
        return out


# ---- ABA routing number checksum -----------------------------------------


def _aba_checksum_valid(s: str) -> bool:
    """ABA routing number mod-10 check.

    Sum 3*(d0+d3+d6) + 7*(d1+d4+d7) + 1*(d2+d5+d8) must be divisible
    by 10. See: https://en.wikipedia.org/wiki/ABA_routing_transit_number
    """
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) != 9:
        return False
    weights = [3, 7, 1, 3, 7, 1, 3, 7, 1]
    return sum(d * w for d, w in zip(digits, weights, strict=True)) % 10 == 0


# ---- Recognizer pack ----------------------------------------------------


US_SSN: Final[Recognizer] = Recognizer(
    name="us_ssn",
    field_type="ssn",
    # Standard formats. Excludes obviously-invalid groups (000, 666, 9xx area;
    # 00 group; 0000 serial) per SSA rules to reduce false positives.
    pattern=re.compile(
        r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
        r"|"
        r"\b(?!000|666|9\d{2})\d{3}(?!00)\d{2}(?!0000)\d{4}\b"
    ),
)


ABA_ROUTING: Final[Recognizer] = Recognizer(
    name="aba_routing",
    field_type="routing_number",
    pattern=re.compile(r"\b\d{9}\b"),
    validator=_aba_checksum_valid,
)


US_BANK_ACCOUNT: Final[Recognizer] = Recognizer(
    name="us_bank_account",
    field_type="account_number",
    # 8-17 digits, optionally hyphenated. Conservative: only fires when
    # surrounded by acct/account context to limit false positives.
    pattern=re.compile(
        r"(?i)\b(?:account(?:\s+(?:number|no\.?|#))?|acct(?:\.\s*(?:no\.?|#))?)\s*[:#]?\s*"
        r"(?P<acct>\d[\d-]{7,16}\d)\b"
    ),
)


MICR_LINE: Final[Recognizer] = Recognizer(
    name="micr_line",
    field_type="micr",
    # MICR symbols ⑆ (transit), ⑈ (on-us), ⑉ (amount), ⑇ (dash). When
    # encoded as ASCII (Magtek devices use 'A'/'B'/'C'/'D'), keep both.
    pattern=re.compile(
        r"[⑆⑇⑈⑉][^⑆⑇⑈⑉]+[⑆⑇⑈⑉]"
        r"|"
        r"A\d{9}A\s*[A-D\d-]{5,}",
    ),
)


DOB: Final[Recognizer] = Recognizer(
    name="dob",
    field_type="date_of_birth",
    # Common written DOB forms. Fires only with a "DOB:"/"born"/"D.O.B." cue
    # to avoid redacting every date in a document.
    pattern=re.compile(
        r"(?i)\b(?:dob|d\.o\.b\.?|date\s+of\s+birth|born(?:\s+on)?)\b"
        r"[:\s-]*"
        r"(?P<date>"
        r"(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19|20)\d{2}"
        r"|"
        r"(?:19|20)\d{2}[/-](?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])"
        r")"
    ),
)


DEFAULT_RECOGNIZERS: Final[tuple[Recognizer, ...]] = (
    US_SSN,
    ABA_ROUTING,
    US_BANK_ACCOUNT,
    MICR_LINE,
    DOB,
)
