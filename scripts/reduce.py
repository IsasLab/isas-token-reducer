#!/usr/bin/env python3
"""ISAS Token Reducer — rule-based, zero-dependency context compression.

Tier 1 (always on, fully offline, Python standard library only):
  1. Whitespace normalization  — collapse blank lines, strip trailing space,
     normalize tabs.
  2. Exact duplicate removal    — drop paragraphs that appear more than once.
  3. Near-duplicate removal     — drop paragraphs whose similarity to an
     already-kept paragraph exceeds a threshold (difflib.SequenceMatcher, no
     ML model, no network).
  4. Filler-phrase trimming     — remove low-information connectors. The phrase
     list is MAINTAINED in ../references/techniques.md (between the
     FILLER-LIST markers) so it is documented, not hidden in code.

Tier 2 (optional, opt-in): summarize long blocks via the Claude API. Runs ONLY
when ``--tier2`` is passed AND ``ANTHROPIC_API_KEY`` is set. The anthropic SDK
is lazy-imported, so Tier 1 never requires the network or any dependency.

SAFETY: this tool only removes STRUCTURAL redundancy (repeats, whitespace,
filler connectors). It must never alter numbers, quotes, code, or legal text.
Tier 1 operates at paragraph granularity and never rewrites the words inside a
kept paragraph.

CLI examples:
    python reduce.py input.txt
    python reduce.py input.txt --stats
    python reduce.py input.txt -o output.txt
    python reduce.py input.txt --similarity 0.85 --no-filler
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

# Make count_tokens importable whether run as a script or imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import count_tokens, is_estimate  # noqa: E402

# Fallback filler list, used only if references/techniques.md cannot be read
# (e.g. reduce.py copied out on its own). The maintained source is techniques.md.
_DEFAULT_FILLERS = [
    "es ist wichtig zu beachten, dass",
    "es sei darauf hingewiesen, dass",
    "wie bereits erwähnt",
    "wie bereits erwähnt wurde",
    "an dieser stelle sei gesagt, dass",
    "im großen und ganzen",
    "im grunde genommen",
    "letzten endes",
    "it is important to note that",
    "it should be noted that",
    "it is worth noting that",
    "as previously mentioned",
    "as mentioned above",
    "as already stated",
    "needless to say",
    "at the end of the day",
    "in conclusion",
    "to summarize",
    "basically",
]

_TECHNIQUES_PATH = Path(__file__).resolve().parent.parent / "references" / "techniques.md"
_FILLER_START = "<!-- FILLER-LIST-START -->"
_FILLER_END = "<!-- FILLER-LIST-END -->"


def load_fillers(techniques_path: Path = _TECHNIQUES_PATH) -> list[str]:
    """Load the filler list from techniques.md, falling back to the builtin list."""
    try:
        text = techniques_path.read_text(encoding="utf-8")
    except OSError:
        return list(_DEFAULT_FILLERS)
    if _FILLER_START not in text or _FILLER_END not in text:
        return list(_DEFAULT_FILLERS)
    block = text.split(_FILLER_START, 1)[1].split(_FILLER_END, 1)[0]
    phrases = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        phrases.append(line)
    return phrases or list(_DEFAULT_FILLERS)


# --------------------------------------------------------------------------- #
# Tier 1 techniques
# --------------------------------------------------------------------------- #
def normalize_whitespace(text: str) -> str:
    """Collapse blank runs, strip trailing whitespace, normalize tabs."""
    lines = [ln.replace("\t", "    ").rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _norm_key(paragraph: str) -> str:
    return re.sub(r"\s+", " ", paragraph.strip()).lower()


def remove_exact_duplicates(text: str) -> str:
    """Drop paragraphs whose normalized text has already appeared."""
    seen: set[str] = set()
    kept: list[str] = []
    for para in _split_paragraphs(text):
        key = _norm_key(para)
        if key in seen:
            continue
        seen.add(key)
        kept.append(para)
    return "\n\n".join(kept)


def remove_near_duplicates(text: str, threshold: float = 0.9) -> str:
    """Drop paragraphs that are >= ``threshold`` similar to a kept paragraph."""
    kept: list[tuple[str, str]] = []  # (original, norm_key)
    for para in _split_paragraphs(text):
        key = _norm_key(para)
        is_dup = any(
            SequenceMatcher(None, key, prev_key).ratio() >= threshold
            for _, prev_key in kept
        )
        if not is_dup:
            kept.append((para, key))
    return "\n\n".join(p for p, _ in kept)


def trim_filler(text: str, fillers: list[str]) -> str:
    """Remove filler connector phrases (case-insensitive) and tidy spacing."""
    for phrase in fillers:
        pattern = re.compile(re.escape(phrase) + r"[,]?[ \t]*", re.IGNORECASE)
        text = pattern.sub("", text)
    # tidy up doubled spaces / space-before-punctuation left behind
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    return text


# --------------------------------------------------------------------------- #
# Tier 2 (optional, network) — lazy imported, never required for Tier 1
# --------------------------------------------------------------------------- #
def _tier2_summarize(text: str, char_threshold: int) -> str:
    """Summarize paragraphs longer than ``char_threshold`` via the Claude API.

    No-ops (with a stderr note) if the SDK is missing or a call fails, so a
    partial/offline environment degrades gracefully instead of erroring.
    """
    try:
        import anthropic  # noqa: PLC0415 - optional, only reached when opted in
    except ImportError:
        sys.stderr.write("[tier2] anthropic SDK not installed; skipping Tier 2.\n")
        return text

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the env
    out: list[str] = []
    for para in _split_paragraphs(text):
        if len(para) <= char_threshold:
            out.append(para)
            continue
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Condense the following text to its essential "
                            "information. Preserve every number, name, quote, "
                            "and factual claim exactly. Do not add commentary.\n\n"
                            + para
                        ),
                    }
                ],
            )
            out.append(resp.content[0].text.strip())
        except Exception as exc:  # network / auth / rate limit
            sys.stderr.write(f"[tier2] call failed ({exc}); keeping original block.\n")
            out.append(para)
    return "\n\n".join(out)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def reduce_text(
    text: str,
    *,
    whitespace: bool = True,
    dedup: bool = True,
    near_dedup: bool = True,
    filler: bool = True,
    similarity: float = 0.9,
    fillers: list[str] | None = None,
    tier2: bool = False,
    tier2_char_threshold: int = 1200,
) -> str:
    """Apply the enabled reduction techniques and return the reduced text."""
    if fillers is None:
        fillers = load_fillers()
    if whitespace:
        text = normalize_whitespace(text)
    if dedup:
        text = remove_exact_duplicates(text)
    if near_dedup:
        text = remove_near_duplicates(text, similarity)
    if filler:
        text = trim_filler(text, fillers)
    if whitespace:
        text = normalize_whitespace(text)
    if tier2:
        if os.environ.get("ANTHROPIC_API_KEY"):
            text = _tier2_summarize(text, tier2_char_threshold)
        else:
            sys.stderr.write(
                "[tier2] ANTHROPIC_API_KEY not set; skipping Tier 2 (Tier 1 done).\n"
            )
    return text


def _format_stats(before: str, after: str) -> str:
    b_tok, b_method = count_tokens(before)
    a_tok, _ = count_tokens(after)
    saved = b_tok - a_tok
    pct = (saved / b_tok * 100) if b_tok else 0.0
    est = " (estimated)" if is_estimate(b_method) else ""
    return (
        f"[stats]{est} tokens: {b_tok} -> {a_tok} "
        f"(saved {saved}, {pct:.1f}%)  chars: {len(before)} -> {len(after)}  "
        f"[{b_method}]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reduce tokens by removing structural redundancy (rule-based)."
    )
    parser.add_argument("input", nargs="?", help="input file; omit to read stdin")
    parser.add_argument("-o", "--output", help="write result to a file instead of stdout")
    parser.add_argument("--stats", action="store_true", help="print before/after token stats to stderr")
    parser.add_argument("--no-whitespace", action="store_true", help="disable whitespace normalization")
    parser.add_argument("--no-dedup", action="store_true", help="disable exact duplicate removal")
    parser.add_argument("--no-near-dedup", action="store_true", help="disable near-duplicate removal")
    parser.add_argument("--no-filler", action="store_true", help="disable filler-phrase trimming")
    parser.add_argument("--similarity", type=float, default=0.9, help="near-duplicate threshold (0-1, default 0.9)")
    parser.add_argument("--tier2", action="store_true", help="opt into Tier 2 API summarization (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--tier2-chars", type=int, default=1200, help="Tier 2: min block length in chars to summarize")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            original = fh.read()
    else:
        original = sys.stdin.read()

    reduced = reduce_text(
        original,
        whitespace=not args.no_whitespace,
        dedup=not args.no_dedup,
        near_dedup=not args.no_near_dedup,
        filler=not args.no_filler,
        similarity=args.similarity,
        tier2=args.tier2,
        tier2_char_threshold=args.tier2_chars,
    )

    if args.stats:
        sys.stderr.write(_format_stats(original, reduced) + "\n")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(reduced + "\n")
    else:
        sys.stdout.write(reduced + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
