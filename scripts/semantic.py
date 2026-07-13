#!/usr/bin/env python3
"""ISAS Token Reducer — Tier 2 semantic core (LOSSY, opt-in, fidelity-guarded).

This module is the OFFLINE-TESTABLE spine shared by two semantic paths:

  * PRIMARY (in Claude Code): the skill spawns a cheap Haiku ``context-condenser``
    subagent that reads raw material in its own isolated context and returns only
    a dense digest. That subagent is instructed with the same fidelity contract
    (``SYSTEM_PROMPT``) and prompt shape (``build_compression_prompt``) defined
    here, so its behavior is describable and testable offline.
  * SECONDARY (outside Claude Code): ``compress_live`` lazy-imports the anthropic
    SDK and calls Haiku directly. It NEVER crashes on a missing SDK / API key —
    it degrades to the Tier-1 text with a one-line message.

WHY SEMANTIC COMPRESSION IS NOT FREE
------------------------------------
Condensing text always COSTS tokens (something must read the raw input once).
It only NET-saves when a genuinely CHEAPER model condenses for a pricier one,
OR the digest is reused across several downstream turns. One-shot, same-model,
single-pass use is ALWAYS net-negative — the expensive model would have read the
raw context once anyway. Callers (e.g. ``reduce.py --auto``) must gate on that.

SAFETY (the point of this module)
---------------------------------
Semantic rewriting is lossy by nature, so a deterministic HARD guardrail sits
after the model, never trusting the model to police itself:

  ``extract_required_spans`` pulls every number, code span, quoted string, and
  proper noun out of the SOURCE, and ``verify_fidelity`` asserts each survives
  verbatim in the digest (numbers checked order-aware, to catch swaps like
  "5 to 10" vs "10 to 5"). On any missing span, ``compress_live`` FAILS CLOSED:
  it discards the digest and returns the Tier-1 / original text unchanged.

Every symbol here runs with ZERO network. Only ``compress_live`` and the CLI's
``--live`` mode touch the API, and both lazy-import the SDK.

Importable:
    from semantic import (
        SYSTEM_PROMPT, chunk_text, build_compression_prompt,
        extract_required_spans, verify_fidelity, compress_offline, compress_live,
    )

CLI:
    python semantic.py --dry-run input.txt         # offline plan + prompts + spans
    python semantic.py --verify source.txt out.txt # offline fidelity diff
    python semantic.py --live input.txt -o out.txt # needs anthropic + API key
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

# Read-only reuse of Tier-1 machinery — semantic.py adds NO edits to reduce.py.
# (_NUMTOK_RE is imported for completeness / downstream extension; _numeric_literals
# already wraps it and is what we call.)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reduce import (  # noqa: E402
    _FENCE_RE,
    _INLINE_CODE_RE,
    _NUMTOK_RE,  # noqa: F401  (kept per the read-only import contract)
    _numeric_literals,
    reduce_text,
)
from count_tokens import count_tokens, is_estimate  # noqa: E402

__all__ = [
    "SYSTEM_PROMPT",
    "DEFAULT_MODEL",
    "chunk_text",
    "build_compression_prompt",
    "extract_required_spans",
    "verify_fidelity",
    "FidelityReport",
    "compress_offline",
    "compress_live",
    "CompressionPlan",
]

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# --------------------------------------------------------------------------- #
# The fidelity contract (soft guardrail). Mirrored in agents/context-condenser.md
# so the subagent and the SDK path share exactly the same instructions.
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a lossless-fidelity context condenser. Your job is to make text "
    "shorter WITHOUT losing a single fact.\n"
    "\n"
    "Copy VERBATIM, character for character, every: number, date, amount, unit, "
    "code span (fenced ``` blocks and `inline` code), quoted string, proper noun "
    "(names of people, places, products, organizations), and any legal or "
    "contractual wording.\n"
    "\n"
    "Densify only the connective prose around those anchors: remove redundancy, "
    "filler, and repetition; merge overlapping statements. Do NOT summarize away "
    "detail, do NOT add commentary, interpretation, or inference, and do NOT "
    "reorder or reattach numbers to different facts.\n"
    "\n"
    "Output ONLY the condensed text - no preamble, no notes, no headings you were "
    "not given. If you are unsure whether something is safe to drop, keep it."
)


# --------------------------------------------------------------------------- #
# Chunking — pack whole paragraphs up to max_chars; never split a fenced block.
# --------------------------------------------------------------------------- #
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _atomize(text: str, boundary: str) -> list[str]:
    """Break ``text`` into indivisible atoms.

    A fenced code block is always ONE atom (never split, even if it alone exceeds
    ``max_chars``). Prose between fences is split into paragraphs (default) or
    sentences, so a chunk boundary never lands inside a fenced block or, at
    paragraph granularity, inside a sentence.
    """
    atoms: list[str] = []
    for i, chunk in enumerate(_FENCE_RE.split(text)):
        if i % 2 == 1:  # fenced code block — keep intact
            if chunk.strip():
                atoms.append(chunk)
            continue
        pieces = _PARA_SPLIT_RE.split(chunk)
        for piece in pieces:
            if not piece.strip():
                continue
            if boundary == "sentence":
                for sent in _SENTENCE_SPLIT_RE.split(piece.strip()):
                    if sent.strip():
                        atoms.append(sent.strip())
            else:
                atoms.append(piece.strip())
    return atoms


def chunk_text(text: str, max_chars: int = 6000, *, boundary: str = "paragraph") -> list[str]:
    """Greedily pack whole paragraphs into chunks of at most ``max_chars`` chars.

    Fenced code blocks are never split across chunks. A single atom larger than
    ``max_chars`` becomes its own (oversized) chunk rather than being cut. Returns
    an empty list for empty / whitespace-only input.
    """
    atoms = _atomize(text, boundary)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for atom in atoms:
        alen = len(atom)
        added = alen if not cur else alen + 2  # +2 for the "\n\n" join
        if cur and cur_len + added > max_chars:
            chunks.append("\n\n".join(cur))
            cur = [atom]
            cur_len = alen
        else:
            cur.append(atom)
            cur_len += added
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def build_compression_prompt(chunk: str) -> tuple[str, str]:
    """Return ``(system, user)`` messages for condensing one chunk.

    The chunk is EXPECTED to already be Tier-1-reduced (dedup-then-densify): the
    model's remaining job is to densify connective prose while passing code and
    quotes through verbatim.
    """
    user = (
        "Condense the CONTEXT below to the fewest words that preserve every fact.\n"
        "- Copy every number, code span, quoted string, name, date, and unit "
        "VERBATIM.\n"
        "- Remove redundancy and filler; merge overlapping statements.\n"
        "- Densify connective prose only. Add no commentary or inference.\n"
        "- Output only the condensed text.\n\n"
        "CONTEXT:\n" + chunk
    )
    return SYSTEM_PROMPT, user


# --------------------------------------------------------------------------- #
# Required-span extraction (the hard guardrail's ground truth).
# --------------------------------------------------------------------------- #
def _strip_code(text: str) -> str:
    """Return prose only: fenced blocks and inline `code` removed.

    Proper nouns / double-quoted strings are extracted from this so code
    identifiers (already guarded verbatim as code spans) don't pollute the sets.
    """
    parts: list[str] = []
    for i, chunk in enumerate(_FENCE_RE.split(text)):
        if i % 2 == 1:
            continue
        for j, seg in enumerate(_INLINE_CODE_RE.split(chunk)):
            if j % 2 == 1:
                continue
            parts.append(seg)
    return "".join(parts)


def _code_spans(text: str) -> set[str]:
    spans: set[str] = set()
    for i, chunk in enumerate(_FENCE_RE.split(text)):
        if i % 2 == 1:
            if chunk.strip():
                spans.add(chunk)
            continue
        for m in _INLINE_CODE_RE.findall(chunk):
            if m.strip("` "):  # ignore empty ``/` ` spans
                spans.add(m)
    return spans


_DQUOTE_RE = re.compile(r'"[^"\n]+"')
# Proper-noun signals: multi-word Capitalized runs, ALLCAPS acronyms, and single
# mid-sentence Capitalized tokens. Sentence-/line-initial single caps are skipped
# so ordinary sentence openers ("The", "This") are not mistaken for names.
_MULTIWORD_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:['\-][A-Za-z0-9]+)?"
    r"(?:\s+[A-Z][A-Za-z0-9]*(?:['\-][A-Za-z0-9]+)?)+\b"
)
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_CAPWORD_RE = re.compile(r"\b[A-Z][a-z]+(?:['\-][A-Za-z]+)?\b")


def _quotes(text: str) -> set[str]:
    quotes: set[str] = set()
    for line in text.split("\n"):
        if line.lstrip().startswith(">"):
            stripped = line.strip()
            if stripped:
                quotes.add(stripped)
    for m in _DQUOTE_RE.findall(_strip_code(text)):
        quotes.add(m)
    return quotes


def _proper_nouns(text: str) -> set[str]:
    prose = _strip_code(text)
    out: set[str] = set()
    for m in _MULTIWORD_RE.finditer(prose):
        out.add(m.group(0))
    for m in _ACRONYM_RE.finditer(prose):
        out.add(m.group(0))
    for m in _CAPWORD_RE.finditer(prose):
        raw = prose[: m.start()]
        if not raw.strip():
            continue  # very start of the document
        if raw[-1] == "\n":
            continue  # line-initial (heading / list item / new line)
        prev = raw.rstrip()[-1]
        if prev in ".!?:;\n":
            continue  # sentence-initial
        out.add(m.group(0))
    return out


def extract_required_spans(text: str) -> dict:
    """Extract every span that MUST survive a semantic rewrite verbatim.

    Returns a dict with:
      * ``numbers``      — ordered tuple of numeric literals (via reduce._numeric_literals)
      * ``code``         — set of fenced blocks + inline `code` spans
      * ``quotes``       — set of blockquote lines + double-quoted strings
      * ``proper_nouns`` — set of names/acronyms/mid-sentence capitalized tokens
    """
    return {
        "numbers": _numeric_literals(text),
        "code": _code_spans(text),
        "quotes": _quotes(text),
        "proper_nouns": _proper_nouns(text),
    }


# --------------------------------------------------------------------------- #
# Fidelity verifier (deterministic, offline).
# --------------------------------------------------------------------------- #
FidelityReport = namedtuple(
    "FidelityReport",
    ["ok", "missing_numbers", "missing_code", "missing_quotes", "missing_proper_nouns"],
)


def _missing_numbers_ordered(src: tuple, cond: tuple) -> list[str]:
    """Order-aware subsequence check.

    Each source number must appear in the condensed numbers in the same relative
    order. A number that cannot be matched from the current position onward is
    reported missing — this catches drops AND swaps (e.g. "5 to 10" -> "10 to 5").
    """
    missing: list[str] = []
    j = 0
    for n in src:
        found = -1
        for k in range(j, len(cond)):
            if cond[k] == n:
                found = k
                break
        if found == -1:
            missing.append(n)
        else:
            j = found + 1
    return missing


def verify_fidelity(source: str, condensed: str) -> FidelityReport:
    """Assert every required span from ``source`` survives verbatim in ``condensed``.

    Numbers are checked order-aware; code spans, quotes, and proper nouns must be
    present as verbatim substrings. ``ok`` is True only when nothing is missing.
    """
    src = extract_required_spans(source)
    cond_numbers = _numeric_literals(condensed)

    missing_numbers = _missing_numbers_ordered(src["numbers"], cond_numbers)
    missing_code = sorted(s for s in src["code"] if s not in condensed)
    missing_quotes = sorted(s for s in src["quotes"] if s not in condensed)
    missing_proper = sorted(s for s in src["proper_nouns"] if s not in condensed)

    ok = not (missing_numbers or missing_code or missing_quotes or missing_proper)
    return FidelityReport(ok, missing_numbers, missing_code, missing_quotes, missing_proper)


# --------------------------------------------------------------------------- #
# Offline dry run — everything needed to preview/test with zero network.
# --------------------------------------------------------------------------- #
class CompressionPlan(dict):
    """A dict that also allows attribute access (``plan.chunks`` == ``plan['chunks']``)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc

    __setattr__ = dict.__setitem__


def compress_offline(text: str, *, max_chars: int = 6000, boundary: str = "paragraph") -> CompressionPlan:
    """Build the full compression plan WITHOUT any network.

    Returns a ``CompressionPlan`` with ``chunks`` (list[str]), ``prompts``
    (list of ``(system, user)`` tuples), and ``required_spans`` (the dict from
    ``extract_required_spans``). This is what the unit tests exercise.
    """
    chunks = chunk_text(text, max_chars=max_chars, boundary=boundary)
    prompts = [build_compression_prompt(c) for c in chunks]
    required_spans = extract_required_spans(text)
    return CompressionPlan(chunks=chunks, prompts=prompts, required_spans=required_spans)


# --------------------------------------------------------------------------- #
# Live path — SECONDARY. Lazy-imports the SDK; never crashes on missing key/SDK.
# --------------------------------------------------------------------------- #
_SDK_MISSING_MSG = (
    "[semantic] anthropic SDK/ANTHROPIC_API_KEY not available; "
    "run --dry-run or use Tier 1"
)


def _call_haiku(client, model: str, system: str, user: str, max_tokens: int) -> str:
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def compress_live(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    level: str = "balanced",
    max_chars: int = 6000,
) -> str:
    """Run the real semantic compression via the Haiku SDK, fail-closed on fidelity.

    Pipeline: Tier-1 reduce first (dedup + byte-safe cleanup), then condense each
    chunk with Haiku, then ``verify_fidelity``. If the SDK or ``ANTHROPIC_API_KEY``
    is missing, prints one clear line and returns the Tier-1 text unchanged. On any
    missing required span it DISCARDS the digest and returns the Tier-1 text — the
    lossy result is never used unverified.
    """
    tier1 = reduce_text(text, level=level)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        import anthropic  # noqa: PLC0415 - optional dependency, lazy
    except ImportError:
        sys.stderr.write(_SDK_MISSING_MSG + "\n")
        return tier1
    if not api_key:
        sys.stderr.write(_SDK_MISSING_MSG + "\n")
        return tier1

    try:
        client = anthropic.Anthropic()
    except Exception as exc:  # misconfigured client
        sys.stderr.write(f"[semantic] could not initialize anthropic client ({exc}); using Tier 1.\n")
        return tier1

    chunks = chunk_text(tier1, max_chars=max_chars)
    out: list[str] = []
    for chunk in chunks:
        system, user = build_compression_prompt(chunk)
        budget = max(256, min(8192, len(chunk) // 3 + 256))
        try:
            out.append(_call_haiku(client, model, system, user, budget))
        except Exception as exc:  # network / auth / rate limit — keep this block as-is
            sys.stderr.write(f"[semantic] call failed ({exc}); keeping this block uncondensed.\n")
            out.append(chunk)
    condensed = "\n\n".join(out).strip()

    report = verify_fidelity(tier1, condensed)
    if not report.ok:
        sys.stderr.write(
            "[semantic] FIDELITY CHECK FAILED - discarding digest, returning Tier-1 text. "
            f"missing: {_summarize_missing(report)}\n"
        )
        return tier1
    return condensed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _summarize_missing(report: FidelityReport) -> str:
    parts = []
    if report.missing_numbers:
        parts.append(f"numbers={report.missing_numbers}")
    if report.missing_code:
        parts.append(f"code={len(report.missing_code)}")
    if report.missing_quotes:
        parts.append(f"quotes={len(report.missing_quotes)}")
    if report.missing_proper_nouns:
        parts.append(f"proper_nouns={report.missing_proper_nouns}")
    return ", ".join(parts) if parts else "(none)"


def _read(path: str | None) -> str:
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return sys.stdin.read()


def _print_plan(plan: CompressionPlan, stats: bool) -> None:
    spans = plan.required_spans
    n = len(plan.chunks)
    print("[semantic dry-run] offline plan - NO network, LOSSY tier preview")
    print(f"chunks: {n}")
    for idx, (chunk, (system, user)) in enumerate(zip(plan.chunks, plan.prompts), 1):
        tok, method = count_tokens(chunk)
        est = " (est)" if is_estimate(method) else ""
        print(f"\n--- chunk {idx}/{n}  chars={len(chunk)}  ~tokens={tok}{est} [{method}] ---")
        print("[SYSTEM]")
        print(system)
        print("[USER]")
        print(user)
    print("\n--- required spans (must survive verbatim) ---")
    print(f"numbers ({len(spans['numbers'])}): {list(spans['numbers'])}")
    print(f"code ({len(spans['code'])}): {sorted(spans['code'])}")
    print(f"quotes ({len(spans['quotes'])}): {sorted(spans['quotes'])}")
    print(f"proper_nouns ({len(spans['proper_nouns'])}): {sorted(spans['proper_nouns'])}")
    if stats:
        total = sum(len(c) for c in plan.chunks)
        print(f"\n[stats] total chunk chars: {total}  chunks: {n}")


def _print_live_ledger(original: str, tier1: str, result: str) -> None:
    """Honest NET LEDGER for the semantic tier — never a bare reduction %."""
    o, method = count_tokens(original)
    t, _ = count_tokens(tier1)
    r, _ = count_tokens(result)
    est = " (est)" if is_estimate(method) else ""
    used_digest = result != tier1
    sys.stderr.write(
        "[semantic ledger]" + est + "\n"
        f"  raw input tokens (expensive side, would be read anyway): {o}\n"
        f"  Tier-1 (free, byte-safe) tokens: {t}\n"
        f"  digest tokens delivered to expensive model: {r}"
        + ("  [LOSSY digest]" if used_digest else "  [no lossy digest used -> Tier-1 returned]") + "\n"
        f"  gross expensive-side saving vs raw: {o - r} tokens\n"
        "  NOTE: condensing also SPENT Haiku tokens (separate budget). Net-positive\n"
        "  ONLY if a cheaper model condensed for a pricier one OR the digest is\n"
        "  reused across >= 2 turns. One-shot same-model use is net-NEGATIVE.\n"
    )


def main(argv: list[str] | None = None) -> int:
    # Fidelity reports echo source-derived spans that can contain non-Latin-1
    # characters (e.g. U+2192 "->"). On Windows the default cp1252 stdout would
    # crash with UnicodeEncodeError, so make console output encode-safe.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Tier 2 semantic core: offline plan/verify + optional live Haiku condense (LOSSY)."
    )
    parser.add_argument("input", nargs="?", help="input file; omit to read stdin")
    parser.add_argument("-o", "--output", help="write result to a file instead of stdout")
    parser.add_argument("--level", default="balanced", choices=["safe", "balanced", "aggressive"],
                        help="Tier-1 level applied before condensing (default: balanced)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Haiku model id for --live")
    parser.add_argument("--max-chars", type=int, default=6000, help="max chars per chunk (default: 6000)")
    parser.add_argument("--stats", action="store_true", help="print token/ledger stats to stderr")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="offline: print chunk plan + prompts + required spans (default)")
    mode.add_argument("--live", action="store_true",
                      help="call Haiku via the anthropic SDK (needs ANTHROPIC_API_KEY)")
    mode.add_argument("--verify", nargs=2, metavar=("SOURCE", "CONDENSED"),
                      help="offline: check a condensed file preserved SOURCE's required spans")
    args = parser.parse_args(argv)

    if args.verify:
        source = _read(args.verify[0])
        condensed = _read(args.verify[1])
        report = verify_fidelity(source, condensed)
        if report.ok:
            print("[semantic verify] OK - all required spans preserved.")
            return 0
        print("[semantic verify] FAILED - missing required spans:")
        print(f"  numbers: {report.missing_numbers}")
        print(f"  code: {report.missing_code}")
        print(f"  quotes: {report.missing_quotes}")
        print(f"  proper_nouns: {report.missing_proper_nouns}")
        return 1

    text = _read(args.input)

    if args.live:
        tier1 = reduce_text(text, level=args.level)
        result = compress_live(text, model=args.model, level=args.level, max_chars=args.max_chars)
        if args.stats:
            _print_live_ledger(text, tier1, result)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(result + "\n")
        else:
            sys.stdout.write(result + "\n")
        return 0

    # Default mode: offline dry run.
    plan = compress_offline(text, max_chars=args.max_chars)
    if args.output:
        # Machine-friendlier: write just the concatenated user prompts.
        with open(args.output, "w", encoding="utf-8") as fh:
            for _system, user in plan.prompts:
                fh.write(user + "\n")
    else:
        _print_plan(plan, args.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
