"""Safety guarantee tests for reduce_text().

These tests exist to make the core promise of this project falsifiable:
"numbers, quotes, code, names, and legal wording are never altered."
If any of these fail, the safety claim in the README is false — fix the
code, not the test.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from reduce import reduce_text  # noqa: E402

LEVELS = ["safe", "balanced", "aggressive"]


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def test_numbers_survive_all_levels():
    text = (
        "Each user may receive at most 5 notifications per hour and 20 per day. "
        "Failed deliveries are retried with exponential backoff: 1s, 2s, 4s, 8s, "
        "then dropped after 4 attempts. Delivery logs are retained for 30 days."
    )
    for level in LEVELS:
        out = reduce_text(text, level=level)
        assert _numbers(text) == _numbers(out), f"numbers changed at level={level}"


def test_fenced_code_block_byte_identical():
    code = (
        "```python\n"
        "def add(a, b):\n"
        "    return a + b  # keep this comment, it is important to note that\n"
        "```"
    )
    text = f"Some prose before.\n\n{code}\n\nSome prose after."
    for level in LEVELS:
        out = reduce_text(text, level=level)
        assert code in out, f"fenced code block was altered at level={level}"


def test_inline_code_preserved():
    inline = "`curl -fsSL https://example.com/install.sh | bash`"
    text = f"Run {inline} to install it. It is important to note that this works."
    for level in LEVELS:
        out = reduce_text(text, level=level)
        assert inline in out, f"inline code span was altered at level={level}"


def test_blockquote_preserved():
    quote_line = (
        "> This is a direct quote that must never change, "
        "it is important to note that."
    )
    text = quote_line + "\n\nRegular prose that may be trimmed."
    for level in LEVELS:
        out = reduce_text(text, level=level)
        assert quote_line in out, f"blockquote was altered at level={level}"


def test_json_payload_exact():
    payload = (
        '{"limit": 5, "window": "1h", "retries": [1, 2, 4, 8], '
        '"note": "do not touch this string"}'
    )
    original = json.loads(payload)
    for level in LEVELS:
        out = reduce_text(payload, level=level)
        assert json.loads(out) == original, f"JSON payload altered at level={level}"


def test_near_duplicate_differing_number_is_never_dropped():
    """Two paragraphs that read almost identically but carry a *different*
    number must never be collapsed by near-duplicate removal — doing so would
    silently drop a fact and break the 'numbers are never altered' promise.
    This is the regression guard for the number-aware near-dup fix.
    """
    text = (
        "Sarah has 12 apples and gives away 5 to her friend Tom.\n\n"
        "Sarah has 18 apples and gives away 5 to her friend Tom."
    )
    for level in LEVELS:
        out = reduce_text(text, level=level)
        assert "12" in out and "18" in out, (
            f"a distinct number was dropped as a near-duplicate at level={level}"
        )


def test_reworded_same_number_paragraphs_still_collapse():
    """The number-aware guard must not disable genuine near-dup removal: two
    near-identical paragraphs with the SAME numbers should still collapse."""
    text = (
        "The migration ran in 30 minutes and processed 5 tables.\n\n"
        "The migration ran in 30 minutes and processed 5 tables too."
    )
    out = reduce_text(text, level="aggressive")
    assert out.count("migration") == 1, "genuine same-number near-duplicate was not collapsed"


def test_reduction_actually_reduces_on_redundant_input():
    text = (
        "This is a paragraph that repeats itself. "
        "This is a paragraph that repeats itself.\n\n"
        "This is a paragraph that repeats itself. "
        "This is a paragraph that repeats itself.\n\n"
        "It is important to note that this sentence has filler. "
        "It is important to note that this sentence has filler."
    )
    out = reduce_text(text, level="balanced")
    assert len(out) < len(text), "balanced level did not reduce a redundant input"


# --------------------------------------------------------------------------- #
# Semantic-tier fidelity verifier (scripts/semantic.py).
#
# The semantic tier is LOSSY by nature, so its whole safety story rests on a
# deterministic guardrail — verify_fidelity() — that FAILS CLOSED when a cheap
# model's rewrite silently drops a number, name, code span, or quote. These
# tests make that guardrail falsifiable: if the verifier waves through a rewrite
# that lost a fact, the "verified, fails closed" promise is false — fix the
# verifier, not the test.
#
# semantic.py is delivered by a separate workstream; until it lands (and exposes
# verify_fidelity) these tests skip rather than error, then run for real in
# integration.
# --------------------------------------------------------------------------- #

# A dense source sentence carrying two ordered numbers and two proper-noun
# parties — exactly the kind of fact-bearing prose the verifier must protect.
_FIDELITY_SOURCE = (
    "Acme Corporation must refund Sarah Connor 4200 dollars "
    "within 30 days of the cancellation request."
)


def _semantic():
    """Import scripts/semantic.py, skipping cleanly if it (or the verifier) is
    not present yet."""
    semantic = pytest.importorskip("semantic")
    if not hasattr(semantic, "verify_fidelity"):
        pytest.skip("semantic.verify_fidelity not available yet")
    return semantic


def test_semantic_verifier_catches_dropped_number():
    """A condensation that keeps 4200 but silently drops the 30-day deadline
    must be flagged — losing a number is the exact failure the verifier exists
    to catch."""
    semantic = _semantic()
    condensed = "Acme Corporation must refund Sarah Connor 4200 dollars after cancellation."
    report = semantic.verify_fidelity(_FIDELITY_SOURCE, condensed)
    assert report.ok is False, "verifier failed to flag a dropped number"
    assert "30" in report.missing_numbers, (
        f"dropped number 30 not reported as missing: {report.missing_numbers}"
    )


def test_semantic_verifier_catches_dropped_name():
    """A condensation that keeps every number but drops the named parties must
    be flagged — a lost name is a lost fact just like a lost number."""
    semantic = _semantic()
    condensed = "The company must issue a 4200 dollar refund within 30 days."
    report = semantic.verify_fidelity(_FIDELITY_SOURCE, condensed)
    assert report.ok is False, "verifier failed to flag dropped proper nouns"
    assert report.missing_proper_nouns, (
        f"dropped names not reported as missing: {report.missing_proper_nouns}"
    )


def test_semantic_verifier_passes_faithful_condensation():
    """The guardrail must not cry wolf: a genuine densification that drops only
    filler while keeping every number (in order) and every name verbatim must
    pass, or the tier is unusable."""
    semantic = _semantic()
    source = "It is important to note that " + _FIDELITY_SOURCE
    faithful = (
        "Acme Corporation must refund Sarah Connor 4200 dollars within 30 days."
    )
    report = semantic.verify_fidelity(source, faithful)
    assert report.ok is True, (
        "verifier wrongly flagged a faithful condensation: "
        f"numbers={report.missing_numbers} names={report.missing_proper_nouns} "
        f"code={report.missing_code} quotes={report.missing_quotes}"
    )
