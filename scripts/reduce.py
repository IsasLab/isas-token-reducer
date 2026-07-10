#!/usr/bin/env python3
"""ISAS Token Reducer — rule-based, zero-dependency context compression.

Tier 1 (always on, fully offline, Python standard library only). Techniques,
selected by `--level`:
  * Whitespace normalization
  * Exact + near-duplicate paragraph removal (difflib, no ML, no network)
  * Exact duplicate-sentence removal
  * Filler-phrase trimming            (list in ../references/techniques.md)
  * Verbose-phrase compression        (map in ../references/phrase_map.md)
  * Lossless JSON whitespace minify   (numbers/strings preserved exactly)
  * Markdown/structure normalization

Levels:
  safe        conservative: whitespace, dedup, near-dup@0.92, filler, JSON.
  balanced    (default) safe + phrase compression + sentence dedup + markdown.
  aggressive  balanced + near-dup@0.85 + drop-all-blank-lines.

Tier 2 (optional, opt-in): `--tier2` + ANTHROPIC_API_KEY summarizes long blocks
via the Claude API. Lazy-imported; Tier 1 never needs the network.

Code mode: `--code` delegates to reduce_code.py (comment/blank-line stripping
for a source-code context copy). See that module.

SAFETY: only structural redundancy or provably meaning-identical wording is
touched. Numbers, quotes, code, names, and legal text are never altered. Phrase
compression and filler trimming skip fenced/inline code and blockquotes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from count_tokens import count_tokens, is_estimate  # noqa: E402

# --------------------------------------------------------------------------- #
# Maintained data (filler phrases + verbose->concise map) live in references/.
# --------------------------------------------------------------------------- #
_REF = Path(__file__).resolve().parent.parent / "references"
_TECHNIQUES_PATH = _REF / "techniques.md"
_PHRASEMAP_PATH = _REF / "phrase_map.md"
_FILLER_START, _FILLER_END = "<!-- FILLER-LIST-START -->", "<!-- FILLER-LIST-END -->"
_SUBS_START, _SUBS_END = "<!-- SUBS-LIST-START -->", "<!-- SUBS-LIST-END -->"

_DEFAULT_FILLERS = [
    "it is important to note that", "it should be noted that",
    "it is worth noting that", "please note that", "as previously mentioned",
    "as mentioned above", "as already stated", "needless to say",
    "at the end of the day", "in conclusion", "to summarize", "basically",
]
# Small built-in fallback map (real map is in references/phrase_map.md).
_DEFAULT_SUBS = [
    ("in order to", "to"), ("due to the fact that", "because"),
    ("in the event that", "if"), ("at this point in time", "now"),
    ("a large number of", "many"), ("has the ability to", "can"),
    ("in spite of the fact that", "although"), ("with regard to", "about"),
]


def _extract_block(text: str, start: str, end: str) -> list[str]:
    if start not in text or end not in text:
        return []
    block = text.split(start, 1)[1].split(end, 1)[0]
    lines = []
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        lines.append(line)
    return lines


def load_fillers(path: Path = _TECHNIQUES_PATH) -> list[str]:
    try:
        lines = _extract_block(path.read_text(encoding="utf-8"), _FILLER_START, _FILLER_END)
    except OSError:
        lines = []
    return lines or list(_DEFAULT_FILLERS)


def load_substitutions(path: Path = _PHRASEMAP_PATH) -> list[tuple[str, str]]:
    """Load verbose->concise pairs from phrase_map.md (`from => to` per line)."""
    subs: list[tuple[str, str]] = []
    try:
        for line in _extract_block(path.read_text(encoding="utf-8"), _SUBS_START, _SUBS_END):
            if "=>" not in line:
                continue
            frm, to = line.split("=>", 1)
            frm, to = frm.strip(), to.strip()
            if frm and frm.lower() != to.lower():
                subs.append((frm, to))
    except OSError:
        pass
    subs = subs or list(_DEFAULT_SUBS)
    # longest phrase first so overlapping phrases resolve to the biggest win
    subs.sort(key=lambda p: len(p[0]), reverse=True)
    return subs


# --------------------------------------------------------------------------- #
# Code-span protection: apply a text transform to prose only, never to fenced
# code, inline `code`, or `>` blockquote lines.
# --------------------------------------------------------------------------- #
_FENCE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.S)
_INLINE_CODE_RE = re.compile(r"(`[^`\n]*`)")


def _apply_to_prose(text: str, fn) -> str:
    out: list[str] = []
    for i, chunk in enumerate(_FENCE_RE.split(text)):
        if i % 2 == 1:  # fenced code block
            out.append(chunk)
            continue
        for j, seg in enumerate(_INLINE_CODE_RE.split(chunk)):
            if j % 2 == 1:  # inline code span
                out.append(seg)
            else:
                # protect markdown blockquote lines within the segment
                lines = seg.split("\n")
                out.append("\n".join(ln if ln.lstrip().startswith(">") else fn(ln) for ln in lines))
    return "".join(out)


# --------------------------------------------------------------------------- #
# Tier 1 techniques
# --------------------------------------------------------------------------- #
def normalize_whitespace(text: str, drop_all_blank: bool = False) -> str:
    lines = [ln.replace("\t", "    ").rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if drop_all_blank:
        text = "\n".join(ln for ln in text.split("\n") if ln.strip())
    return text.strip("\n")


def _split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def remove_exact_duplicates(text: str) -> str:
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
    kept: list[tuple[str, str]] = []
    for para in _split_paragraphs(text):
        key = _norm_key(para)
        if any(SequenceMatcher(None, key, k).ratio() >= threshold for _, k in kept):
            continue
        kept.append((para, key))
    return "\n\n".join(p for p, _ in kept)


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def remove_duplicate_sentences(text: str, min_len: int = 25) -> str:
    """Drop exact duplicate sentences (normalized) across the whole prose.

    Only substantial sentences (>= min_len chars) are deduped, so short repeats
    like 'OK.' or 'Yes.' are preserved. Code spans are protected by the caller.
    """
    seen: set[str] = set()

    def dedup_paragraph(par: str) -> str:
        parts = _SENT_SPLIT_RE.split(par)
        out = []
        for s in parts:
            key = _norm_key(s)
            if len(key) >= min_len and key in seen:
                continue
            if len(key) >= min_len:
                seen.add(key)
            out.append(s)
        return " ".join(out).strip()

    kept = [dedup_paragraph(p) for p in _split_paragraphs(text)]
    return "\n\n".join(p for p in kept if p)


_CAP_MARK = "\x00CAP\x00"  # internal marker: a filler was removed at a sentence start


def trim_filler(text: str, fillers: list[str]) -> str:
    def fn(seg: str) -> str:
        for phrase in fillers:
            pat = re.compile(re.escape(phrase) + r"[,]?[ \t]*", re.IGNORECASE)

            def repl(m: re.Match) -> str:
                # Was this filler at the start of a sentence? If so, mark the
                # spot so the next word gets re-capitalized; else just delete.
                before = m.string[: m.start()].rstrip()
                at_sentence_start = (
                    before == "" or before[-1] in ".!?\n" or before.endswith(_CAP_MARK)
                )
                return _CAP_MARK if at_sentence_start else ""

            seg = pat.sub(repl, seg)
        # Re-capitalize the first letter after a sentence-start removal only.
        seg = re.sub(
            re.escape(_CAP_MARK) + r"(\s*)([a-zà-ÿ])",
            lambda m: m.group(1) + m.group(2).upper(),
            seg,
        )
        seg = seg.replace(_CAP_MARK, "")
        seg = re.sub(r"[ \t]{2,}", " ", seg)
        return re.sub(r"[ \t]+([.,;:!?])", r"\1", seg)

    return _apply_to_prose(text, fn)


def compress_phrases(text: str, subs: list[tuple[str, str]]) -> str:
    """Replace verbose phrases with meaning-identical shorter forms (prose only)."""
    compiled = []
    for frm, to in subs:
        pat = re.compile(r"\b" + re.escape(frm) + r"\b", re.IGNORECASE)
        compiled.append((pat, to))

    def fn(seg: str) -> str:
        for pat, to in compiled:
            def repl(m, _to=to):
                if _to and m.group(0)[:1].isupper():
                    return _to[:1].upper() + _to[1:]
                return _to
            seg = pat.sub(repl, seg)
        seg = re.sub(r"[ \t]{2,}", " ", seg)
        seg = re.sub(r"\s+([.,;:!?])", r"\1", seg)
        return seg

    return _apply_to_prose(text, fn)


def _strip_json_ws(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    in_str = False
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c in " \t\n\r":
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def minify_json(text: str) -> str:
    """Losslessly strip whitespace from whole-document JSON or ```json blocks.

    Only whitespace outside string literals is removed — numbers and strings are
    byte-identical, so no value ever changes.
    """
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            json.loads(stripped)
            return _strip_json_ws(stripped)
        except ValueError:
            pass

    def repl(m: re.Match) -> str:
        body = m.group(1)
        try:
            json.loads(body)
        except ValueError:
            return m.group(0)
        return "```json\n" + _strip_json_ws(body).strip() + "\n```"

    return re.sub(r"```json\s*(.*?)```", repl, text, flags=re.S)


def normalize_markdown(text: str) -> str:
    # one blank line max around ATX headings; strip trailing '#'; tidy list markers
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #
LEVELS = {
    "safe": dict(json=True, whitespace=True, phrases=False, filler=True,
                 dedup=True, sentence_dedup=False, near_dedup=True,
                 similarity=0.92, markdown=False, drop_all_blank=False),
    "balanced": dict(json=True, whitespace=True, phrases=True, filler=True,
                     dedup=True, sentence_dedup=True, near_dedup=True,
                     similarity=0.90, markdown=True, drop_all_blank=False),
    "aggressive": dict(json=True, whitespace=True, phrases=True, filler=True,
                       dedup=True, sentence_dedup=True, near_dedup=True,
                       similarity=0.85, markdown=True, drop_all_blank=True),
}


# --------------------------------------------------------------------------- #
# Tier 2 (optional, network) — lazy imported
# --------------------------------------------------------------------------- #
def _tier2_summarize(text: str, char_threshold: int) -> str:
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        sys.stderr.write("[tier2] anthropic SDK not installed; skipping Tier 2.\n")
        return text
    client = anthropic.Anthropic()
    out: list[str] = []
    for para in _split_paragraphs(text):
        if len(para) <= char_threshold:
            out.append(para)
            continue
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": (
                    "Condense the following text to its essential information. "
                    "Preserve every number, name, quote, and factual claim exactly. "
                    "Do not add commentary.\n\n" + para)}],
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
    level: str = "balanced",
    overrides: dict | None = None,
    fillers: list[str] | None = None,
    subs: list[tuple[str, str]] | None = None,
    tier2: bool = False,
    tier2_char_threshold: int = 1200,
) -> str:
    cfg = dict(LEVELS.get(level, LEVELS["balanced"]))
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    if fillers is None:
        fillers = load_fillers()
    if subs is None:
        subs = load_substitutions()

    if cfg["json"]:
        stripped = text.strip()
        if stripped[:1] in "{[":
            try:
                json.loads(stripped)
                # Whole-document JSON: minify losslessly and stop — it is data,
                # not prose, so no prose pass may touch it.
                return _strip_json_ws(stripped)
            except ValueError:
                pass
        text = minify_json(text)  # only embedded ```json blocks from here
    if cfg["whitespace"]:
        text = normalize_whitespace(text)
    if cfg["phrases"]:
        text = compress_phrases(text, subs)
    if cfg["filler"]:
        text = trim_filler(text, fillers)
    if cfg["dedup"]:
        text = remove_exact_duplicates(text)
    if cfg["sentence_dedup"]:
        text = remove_duplicate_sentences(text)
    if cfg["near_dedup"]:
        text = remove_near_duplicates(text, cfg["similarity"])
    if cfg["markdown"]:
        text = normalize_markdown(text)
    if cfg["whitespace"]:
        text = normalize_whitespace(text, drop_all_blank=cfg["drop_all_blank"])
    if tier2:
        if os.environ.get("ANTHROPIC_API_KEY"):
            text = _tier2_summarize(text, tier2_char_threshold)
        else:
            sys.stderr.write("[tier2] ANTHROPIC_API_KEY not set; skipping Tier 2 (Tier 1 done).\n")
    return text


def _format_stats(before: str, after: str) -> str:
    b, method = count_tokens(before)
    a, _ = count_tokens(after)
    saved = b - a
    pct = (saved / b * 100) if b else 0.0
    est = " (estimated)" if is_estimate(method) else ""
    return (f"[stats]{est} tokens: {b} -> {a} (saved {saved}, {pct:.1f}%)  "
            f"chars: {len(before)} -> {len(after)}  [{method}]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reduce tokens by removing redundancy (rule-based, offline).")
    parser.add_argument("input", nargs="?", help="input file; omit to read stdin")
    parser.add_argument("-o", "--output", help="write result to a file instead of stdout")
    parser.add_argument("--stats", action="store_true", help="print before/after token stats to stderr")
    parser.add_argument("--level", default="balanced", choices=["safe", "balanced", "aggressive"],
                        help="reduction aggressiveness (default: balanced)")
    parser.add_argument("--similarity", type=float, default=None, help="override near-duplicate threshold (0-1)")
    parser.add_argument("--no-whitespace", action="store_true")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--no-near-dedup", action="store_true")
    parser.add_argument("--no-sentence-dedup", action="store_true")
    parser.add_argument("--no-filler", action="store_true")
    parser.add_argument("--no-phrases", action="store_true")
    parser.add_argument("--no-json", action="store_true")
    parser.add_argument("--tier2", action="store_true", help="opt into Tier 2 API summarization (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--tier2-chars", type=int, default=1200)
    parser.add_argument("--code", action="store_true", help="code mode: strip comments/blank lines (see reduce_code.py)")
    args = parser.parse_args(argv)

    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            original = fh.read()
    else:
        original = sys.stdin.read()

    if args.code:
        from reduce_code import detect_lang, reduce_code
        reduced = reduce_code(original, lang=detect_lang(args.input))
    else:
        overrides = {}
        if args.no_whitespace:
            overrides["whitespace"] = False
        if args.no_dedup:
            overrides["dedup"] = False
        if args.no_near_dedup:
            overrides["near_dedup"] = False
        if args.no_sentence_dedup:
            overrides["sentence_dedup"] = False
        if args.no_filler:
            overrides["filler"] = False
        if args.no_phrases:
            overrides["phrases"] = False
        if args.no_json:
            overrides["json"] = False
        if args.similarity is not None:
            overrides["similarity"] = args.similarity
        reduced = reduce_text(original, level=args.level, overrides=overrides,
                              tier2=args.tier2, tier2_char_threshold=args.tier2_chars)

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
