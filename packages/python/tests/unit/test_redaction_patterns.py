"""Finance pattern recognizer tests."""

from __future__ import annotations

import pytest

from agent_capture.redaction.pattern import PatternRedactor
from agent_capture.redaction.patterns_finance import (
    ABA_ROUTING,
    DOB,
    MICR_LINE,
    US_BANK_ACCOUNT,
    US_SSN,
    _aba_checksum_valid,
)
from agent_capture.redaction.strategies import FullRedaction


def _full_for_all(_field_type: str) -> FullRedaction:
    return FullRedaction()


def test_ssn_dashed_and_undashed() -> None:
    r = US_SSN
    # 123-45-6789 (dashed) and 555121234 (undashed) are both valid per SSA
    # area rules. 987... would be rejected because 9xx area is reserved.
    assert [m.value for m in r.find_all("SSN 123-45-6789 / 555121234")] == [
        "123-45-6789",
        "555121234",
    ]


@pytest.mark.parametrize(
    "invalid",
    [
        "000-12-3456",  # invalid area
        "666-12-3456",
        "900-12-3456",  # 9xx area
        "123-00-1234",  # zero group
        "123-12-0000",  # zero serial
    ],
)
def test_ssn_excludes_invalid_groups(invalid: str) -> None:
    assert US_SSN.find_all(invalid) == []


def test_aba_routing_validates_checksum() -> None:
    # 011000015 is a known-valid ABA routing number (Federal Reserve, Boston).
    assert _aba_checksum_valid("011000015") is True
    # 011000016 fails the checksum.
    assert _aba_checksum_valid("011000016") is False
    matches = ABA_ROUTING.find_all("send to ABA 011000015 today")
    assert [m.value for m in matches] == ["011000015"]
    assert ABA_ROUTING.find_all("not-routing 011000016") == []


def test_bank_account_requires_context() -> None:
    # Bare 12-digit string with no acct context: no match.
    assert US_BANK_ACCOUNT.find_all("hello 123456789012 world") == []
    # With acct context: match.
    matches = US_BANK_ACCOUNT.find_all("Account #: 123456789012")
    assert len(matches) == 1


def test_dob_requires_cue_word() -> None:
    assert DOB.find_all("12/01/1985") == []
    matches = DOB.find_all("DOB: 12/01/1985")
    assert len(matches) == 1


def test_micr_basic_match() -> None:
    matches = MICR_LINE.find_all("A011000015A 123456789-")
    assert len(matches) == 1


# ---- PatternRedactor end-to-end --------------------------------------


def test_redactor_replaces_each_match() -> None:
    r = PatternRedactor(strategy_for=_full_for_all)
    text = "SSN: 123-45-6789, ABA: 011000015"
    out = r.redact(text)
    assert "123-45-6789" not in out
    assert "011000015" not in out
    assert "[REDACTED:ssn]" in out
    assert "[REDACTED:routing_number]" in out


def test_redactor_empty_input_is_passthrough() -> None:
    r = PatternRedactor(strategy_for=_full_for_all)
    assert r.redact("") == ""
    assert r.redact("nothing sensitive here") == "nothing sensitive here"


def test_redactor_overlap_resolution_keeps_longer() -> None:
    r = PatternRedactor(strategy_for=_full_for_all)
    # 9-digit string matches BOTH SSN-undashed and ABA-routing recognizers.
    # SSN format is more specific; ABA needs checksum. Use a value that
    # passes ABA checksum: 011000015. SSN-undashed would reject because
    # area starts with 011 (not 000/666/9xx — actually 011 is allowed).
    # So both regexes match the same bytes. Without overlap resolution we'd
    # get two replacements and corrupt the string. With it, one wins.
    out = r.redact("number: 011000015")
    # Either way the digits are gone.
    assert "011000015" not in out
    # And we got exactly one replacement token in that range.
    assert out.count("[REDACTED:") == 1
