#!/usr/bin/env python3
"""ISAS Token Reducer — rule-based, zero-dependency context compression.

Tier 1 (always on, fully offline, Python standard library only). Techniques,
selected by `--level`:
  * Zero-width / BOM invisible-character stripping (pure-waste code points)
  * Whitespace normalization
  * Exact + near-duplicate paragraph removal (difflib, no ML, no network)
  * Exact + near-duplicate sentence removal  (number/negation-guarded)
  * Filler-phrase trimming            (list in ../references/techniques.md)
  * Verbose-phrase compression        (map in ../references/phrase_map.md)
  * Duplicate list-item removal        (bulleted / numbered lists)
  * Lossless Markdown table compaction (render-identical padding trim)
  * Lossless JSON whitespace minify   (numbers/strings preserved exactly)
  * Markdown/structure normalization

Levels:
  safe        conservative: whitespace, dedup, near-dup@0.92, filler, JSON.
  balanced    (default) safe + phrase compression + sentence dedup + near-dup
              sentence@0.95 + list dedup + table compaction + markdown.
  aggressive  balanced + near-dup@0.85 + near-dup sentence@0.90 + list near-dup
              + drop-all-blank-lines.

`--auto` (advisory only): after the free Tier 1 pass it measures realized
savings on the shared tokenizer and prints ONE honest verdict to stderr —
when unique prose is near its information-theory floor it says so and points at
the (lossy, opt-in) `--semantic` tier instead of overselling Tier 1.

Tier 2 (optional, opt-in): `--tier2` + ANTHROPIC_API_KEY summarizes long blocks
via the Claude API — a programmatic SDK fallback for pipelines outside Claude
Code (the primary semantic route is the skill-orchestrated Haiku
context-condenser). Lazy-imported; Tier 1 never needs the network.

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
import zlib
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
# Pure-waste code points that render as nothing but each cost a token:
# zero-width space/non-joiner/joiner, BOM/zero-width-no-break-space, word joiner.
# Smart quotes (U+2018/2019/201C/201D) and non-breaking space (U+00A0) are
# DELIBERATELY excluded — they can be meaningful or quoted, so the "quotes are
# never altered" promise stays absolute.
_INVISIBLE_RE = re.compile("[​‌‍﻿⁠]")


def normalize_invisibles(text: str) -> str:
    """Strip zero-width / BOM characters everywhere (safe at every level).

    These code points are invisible and semantically empty, so removing them
    cannot change a visible character, a number, or a code token — it only
    reclaims the token each one costs.
    """
    return _INVISIBLE_RE.sub("", text)


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


# Numeric literals in a paragraph, in order of appearance, normalized
# (thousands-commas stripped). Used to guard near-duplicate removal: two
# paragraphs that read almost identically but carry different numbers, or the
# same numbers attached to different facts ("...grew from 5 to 10..." vs
# "...fell from 10 to 5...") are NOT redundant — collapsing them would
# silently drop a fact. Comparing the numbers as an ordered sequence (not a
# set) catches reordered/swapped numbers, not just added/removed ones, which
# keeps the core safety promise ("numbers are never altered") true even for
# fuzzy dedup.
_NUMTOK_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numeric_literals(s: str) -> tuple[str, ...]:
    return tuple(m.replace(",", "") for m in _NUMTOK_RE.findall(s))


# Negation markers used to guard fuzzy sentence dedup. Two sentences that read
# almost identically but differ in their negation content ("must" vs "must not",
# "can" vs "cannot") are opposites, not duplicates — collapsing them would flip
# a fact. We compare the SORTED set of negation markers in each sentence; if the
# signatures differ at all, the sentences are never treated as near-duplicates.
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|without|cannot|can't|won't|don't|doesn't|didn't|isn't|"
    r"aren't|wasn't|weren't|hasn't|haven't|hadn't|shouldn't|wouldn't|couldn't|"
    r"mustn't|shan't|needn't|none|nobody|nothing|nowhere|nor|neither)\b|n't",
    re.IGNORECASE,
)


def _negation_signature(s: str) -> tuple[str, ...]:
    return tuple(sorted(m.group(0).lower() for m in _NEGATION_RE.finditer(s)))


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
    # A paragraph is only dropped as a near-duplicate of a kept one when it is
    # BOTH textually similar (>= threshold) AND carries the identical ordered
    # sequence of numeric literals. Comparing order (not just the set) means
    # paragraphs that swap which number goes with which fact are never
    # collapsed, so no number — or its meaning — is ever lost to fuzzy dedup.
    kept: list[tuple[str, str, tuple[str, ...]]] = []
    for para in _split_paragraphs(text):
        key = _norm_key(para)
        nums = _numeric_literals(para)
        if any(nums == knums and SequenceMatcher(None, key, k).ratio() >= threshold
               for _, k, knums in kept):
            continue
        kept.append((para, key, nums))
    return "\n\n".join(p for p, _, _ in kept)


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


def remove_near_duplicate_sentences(
    text: str,
    threshold: float,
    *,
    negation_guard: bool = True,
    min_len: int = 25,
) -> str:
    """Drop sentence-level near-duplicates that paragraph dedup can't see.

    Complements `remove_near_duplicates` (paragraph-only) and
    `remove_duplicate_sentences` (exact-only): a restated sentence buried inside
    an otherwise-different paragraph is invisible to both.

    A sentence ``s`` is dropped as a near-duplicate of an already-kept sentence
    ``k`` ONLY when ALL of these hold, so no fact is ever silently lost:
      * both are substantial (>= ``min_len`` normalized chars — short strings
        false-match under fuzzy comparison);
      * ``SequenceMatcher`` ratio of their normalized keys >= ``threshold``
        (deliberately higher than paragraph thresholds);
      * they carry the identical ORDERED sequence of numeric literals (so a
        swapped or changed number blocks the collapse);
      * (``negation_guard``) they carry the identical set of negation markers,
        so "you must ship it" and "you must not ship it" never merge.

    Runs entirely inside ``_apply_to_prose`` so fenced/inline code and ``>``
    blockquotes are never touched. Kept-sentence state is shared across the whole
    document via closure, so a restatement far from the original is still caught.
    """
    kept: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    def fn(seg: str) -> str:
        parts = _SENT_SPLIT_RE.split(seg)
        out: list[str] = []
        dropped_any = False
        for s in parts:
            key = _norm_key(s)
            if len(key) < min_len:
                out.append(s)
                continue
            nums = _numeric_literals(s)
            neg = _negation_signature(key) if negation_guard else ()
            is_dup = any(
                knums == nums
                and (not negation_guard or kneg == neg)
                and SequenceMatcher(None, key, k).ratio() >= threshold
                for k, knums, kneg in kept
            )
            if is_dup:
                dropped_any = True
                continue
            kept.append((key, nums, neg))
            out.append(s)
        # Only rebuild (and thus normalize inter-sentence spacing) when we
        # actually removed something; otherwise return the line byte-for-byte.
        if not dropped_any:
            return seg
        return " ".join(x for x in out if x != "").strip()

    return _apply_to_prose(text, fn)


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


# A thematic break ("---", "***", "___", or spaced "* * *"): must never be
# mistaken for a list item when unifying bullet markers.
_THEMATIC_BREAK_RE = re.compile(r"^\s{0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_ATX_TRAILING_HASH_RE = re.compile(r"^(#{1,6}[ \t].*?)[ \t]+#+[ \t]*$")
_BULLET_RE = re.compile(r"^(\s*)([-*+])([ \t]+)(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+[.)])([ \t]{2,})(.*)$")


def normalize_markdown(text: str) -> str:
    """Render-lossless Markdown tidy (numbers/quotes/code never touched).

    * strips trailing whitespace and collapses 3+ blank lines to one;
    * strips a redundant trailing ``#`` sequence from ATX headings
      (``## Title ##`` -> ``## Title``);
    * unifies leading bullet markers ``*``/``+`` to ``-`` (thematic breaks like
      ``* * *`` are guarded and left untouched);
    * collapses 2+ spaces after a list marker to one.

    Line-oriented transforms run through ``_apply_to_prose`` so fenced/inline
    code and ``>`` blockquotes are never rewritten. It never force-inserts blank
    lines (that would add tokens to a *reducer*), only removes redundancy.
    """
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)

    def fn(line: str) -> str:
        m = _ATX_TRAILING_HASH_RE.match(line)
        if m:
            line = m.group(1)
        if _THEMATIC_BREAK_RE.match(line):
            return line  # leave "* * *" / "- - -" alone
        bm = _BULLET_RE.match(line)
        if bm:
            return f"{bm.group(1)}- {bm.group(4)}"
        om = _ORDERED_RE.match(line)
        if om:
            return f"{om.group(1)}{om.group(2)} {om.group(4)}"
        return line

    return _apply_to_prose(text, fn)


# --------------------------------------------------------------------------- #
# GFM table compaction (fully lossless — render stays byte-equivalent)
# --------------------------------------------------------------------------- #
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _looks_like_row(line: str) -> bool:
    return "|" in line and line.strip() != ""


def _split_table_cells(inner: str) -> list[str]:
    """Split a table row body on '|' that is NOT inside an inline-code span or
    backslash-escaped, so a '|' inside `code` (or written as \\|) never splits."""
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i, n = 0, len(inner)
    while i < n:
        c = inner[i]
        if c == "`":
            in_code = not in_code
            buf.append(c)
        elif c == "\\" and not in_code and i + 1 < n:
            buf.append(c)
            buf.append(inner[i + 1])
            i += 2
            continue
        elif c == "|" and not in_code:
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    cells.append("".join(buf))
    return cells


def _min_separator_cell(cell: str) -> str:
    c = cell.strip()
    left = c.startswith(":")
    right = c.endswith(":")
    if left and right:
        return ":-:"
    if left:
        return ":-"
    if right:
        return "-:"
    return "-"


def _compact_row(line: str, is_sep: bool) -> str:
    indent = line[: len(line) - len(line.lstrip())]
    core = line.strip()
    lead = core.startswith("|")
    trail = core.endswith("|")
    inner = core[1:] if lead else core
    if trail and inner.endswith("|"):
        inner = inner[:-1]
    cells = _split_table_cells(inner)
    new_cells = []
    for cell in cells:
        if is_sep:
            new_cells.append(_min_separator_cell(cell))
        elif "`" in cell:
            new_cells.append(cell)  # inline code: leave interior untouched
        else:
            new_cells.append(cell.strip())
    body = "|".join(new_cells)
    return indent + ("|" if lead else "") + body + ("|" if trail else "")


def compact_markdown_tables(text: str) -> str:
    """Trim inter-cell padding and minimize separator dashes in GFM tables.

    Lossless: a rendered table is byte-identical before and after. Skips any
    table inside a fenced code block and never alters a cell that contains an
    inline-code span.
    """
    def process(chunk: str) -> str:
        lines = chunk.split("\n")
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            if (
                i + 1 < n
                and _looks_like_row(lines[i])
                and _TABLE_SEP_RE.match(lines[i + 1])
            ):
                block = [lines[i], lines[i + 1]]
                j = i + 2
                while j < n and _looks_like_row(lines[j]):
                    block.append(lines[j])
                    j += 1
                out.append(_compact_row(block[0], is_sep=False))
                out.append(_compact_row(block[1], is_sep=True))
                for row in block[2:]:
                    out.append(_compact_row(row, is_sep=False))
                i = j
            else:
                out.append(lines[i])
                i += 1
        return "\n".join(out)

    parts = _FENCE_RE.split(text)
    return "".join(p if k % 2 == 1 else process(p) for k, p in enumerate(parts))


# --------------------------------------------------------------------------- #
# List-item dedup (a bulleted/numbered list is one paragraph, so paragraph
# dedup can't see duplicate bullets).
# --------------------------------------------------------------------------- #
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(.*)$")


def _dedup_list_block(block: list[str], near: bool, threshold: float) -> list[str]:
    kept: list[tuple[str, tuple[str, ...]]] = []
    result: list[str] = []
    for line in block:
        m = _LIST_ITEM_RE.match(line)
        content = m.group(4)
        key = _norm_key(content)
        nums = _numeric_literals(content)
        has_code = "`" in content
        is_dup = False
        for kkey, knums in kept:
            if key == kkey:  # exact (normalized) duplicate item
                is_dup = True
                break
            if (
                near
                and not has_code  # never fuzzy-drop a bullet holding inline code
                and knums == nums  # number-aware: differing numbers are kept
                and SequenceMatcher(None, key, kkey).ratio() >= threshold
            ):
                is_dup = True
                break
        if is_dup:
            continue
        kept.append((key, nums))
        result.append(line)
    return result


def dedup_list_items(text: str, *, near: bool = False, threshold: float = 0.90) -> str:
    """Drop duplicate items within a contiguous bulleted/numbered list.

    Exact (normalized) duplicate bullets are always removed; number-aware
    near-duplicates only when ``near`` is set (aggressive level). Dedup is scoped
    to a single unbroken run of list lines, so two separate lists never dedup
    against each other. Fenced code blocks are skipped entirely.
    """
    def process(chunk: str) -> str:
        lines = chunk.split("\n")
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            if _LIST_ITEM_RE.match(lines[i]):
                j = i
                while j < n and _LIST_ITEM_RE.match(lines[j]):
                    j += 1
                out.extend(_dedup_list_block(lines[i:j], near, threshold))
                i = j
            else:
                out.append(lines[i])
                i += 1
        return "\n".join(out)

    parts = _FENCE_RE.split(text)
    return "".join(p if k % 2 == 1 else process(p) for k, p in enumerate(parts))


# --------------------------------------------------------------------------- #
# Optional HTML-comment stripping (opt-in only — comments are usually noise but
# occasionally load-bearing, so never automatic).
# --------------------------------------------------------------------------- #
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def strip_html_comments(text: str) -> str:
    """Remove <!-- ... --> from prose. Skips fenced and inline code spans."""
    parts = _FENCE_RE.split(text)
    out: list[str] = []
    for i, chunk in enumerate(parts):
        if i % 2 == 1:  # fenced code
            out.append(chunk)
            continue
        segs = _INLINE_CODE_RE.split(chunk)
        out.append(
            "".join(
                s if j % 2 == 1 else _HTML_COMMENT_RE.sub("", s)
                for j, s in enumerate(segs)
            )
        )
    return "".join(out)


# --------------------------------------------------------------------------- #
# Levels
# --------------------------------------------------------------------------- #
LEVELS = {
    "safe": dict(json=True, whitespace=True, invisibles=True, phrases=False,
                 filler=True, dedup=True, sentence_dedup=False,
                 near_dedup_sentences=False, similarity_sent=0.95,
                 near_dedup=True, similarity=0.92, dedup_lists=False,
                 dedup_lists_near=False, compact_tables=False,
                 strip_html_comments=False, markdown=False, drop_all_blank=False),
    "balanced": dict(json=True, whitespace=True, invisibles=True, phrases=True,
                     filler=True, dedup=True, sentence_dedup=True,
                     near_dedup_sentences=True, similarity_sent=0.95,
                     near_dedup=True, similarity=0.90, dedup_lists=True,
                     dedup_lists_near=False, compact_tables=True,
                     strip_html_comments=False, markdown=True, drop_all_blank=False),
    "aggressive": dict(json=True, whitespace=True, invisibles=True, phrases=True,
                       filler=True, dedup=True, sentence_dedup=True,
                       near_dedup_sentences=True, similarity_sent=0.90,
                       near_dedup=True, similarity=0.85, dedup_lists=True,
                       dedup_lists_near=True, compact_tables=True,
                       strip_html_comments=False, markdown=True, drop_all_blank=True),
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
    if cfg.get("invisibles", True):
        text = normalize_invisibles(text)
    if cfg["whitespace"]:
        text = normalize_whitespace(text)
    if cfg.get("strip_html_comments", False):
        text = strip_html_comments(text)
    if cfg["phrases"]:
        text = compress_phrases(text, subs)
    if cfg["filler"]:
        text = trim_filler(text, fillers)
    if cfg["dedup"]:
        text = remove_exact_duplicates(text)
    if cfg["sentence_dedup"]:
        text = remove_duplicate_sentences(text)
    if cfg.get("near_dedup_sentences", False):
        text = remove_near_duplicate_sentences(text, cfg.get("similarity_sent", 0.95))
    if cfg["near_dedup"]:
        text = remove_near_duplicates(text, cfg["similarity"])
    if cfg.get("dedup_lists", False):
        text = dedup_list_items(text, near=cfg.get("dedup_lists_near", False))
    if cfg.get("compact_tables", False):
        text = compact_markdown_tables(text)
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


# --------------------------------------------------------------------------- #
# Measurement + --auto advisor
# --------------------------------------------------------------------------- #
# Verdict thresholds (percent of tokens saved by the free Tier 1 pass).
_AUTO_HIGH_PCT = 10.0   # >= this: Tier 1 clearly handled it
_AUTO_LOW_PCT = 5.0     # < this: little structural redundancy to exploit
_AUTO_SMALL_TOK = 2000  # below this a low-savings input is "small unique prose"
_STATS_NOTE_MIN_TOK = 200  # don't nag about tiny inputs


def _redundancy_signals(before: str, after: str) -> dict:
    """Cheap, honest measurement shared by --stats and --auto.

    Percentages are computed same-method-both-sides on the shared tokenizer, so
    the ratio is valid at whatever rung count_tokens produced. ``zlib_density``
    is a stdlib compression-ratio proxy for overall information density (lower
    means more compressible/redundant) — an auxiliary signal only.
    """
    b, method = count_tokens(before)
    a, _ = count_tokens(after)
    saved = b - a
    pct = (saved / b * 100) if b else 0.0
    raw = before.encode("utf-8")
    density = round(len(zlib.compress(raw, 6)) / len(raw), 3) if raw else 1.0
    return {
        "before_tokens": b,
        "after_tokens": a,
        "saved_tokens": saved,
        "saved_pct": round(pct, 2),
        "method": method,
        "is_estimate": is_estimate(method),
        "zlib_density": density,
    }


def _stats_line(sig: dict, before: str, after: str) -> str:
    est = " (estimated)" if sig["is_estimate"] else ""
    return (
        f"[stats]{est} tokens: {sig['before_tokens']} -> {sig['after_tokens']} "
        f"(saved {sig['saved_tokens']}, {sig['saved_pct']:.1f}%)  "
        f"chars: {len(before)} -> {len(after)}  [{sig['method']}]"
    )


def _auto_verdict(sig: dict) -> tuple[str, str]:
    """Return (verdict_code, honest advice line). Advisory only — never runs the
    lossy semantic tier, only names the net-save condition so the user opts in
    with eyes open."""
    pct = sig["saved_pct"]
    b = sig["before_tokens"]
    if pct >= _AUTO_HIGH_PCT:
        return ("tier1_sufficient",
                f"Tier 1 handled it ({pct:.1f}% saved, structural redundancy high). "
                f"Done - no semantic tier needed.")
    if pct < _AUTO_LOW_PCT and b < _AUTO_SMALL_TOK:
        return ("send_as_is",
                f"Only {pct:.1f}% saved on ~{b} small tokens: this is unique prose "
                f"near the information-theory floor. Send it as-is - do NOT run the "
                f"semantic tier, it would cost more tokens than it saves.")
    if pct < _AUTO_LOW_PCT:
        return ("consider_semantic",
                f"Only {pct:.1f}% saved on ~{b} tokens. Tier 1 cannot shrink unique "
                f"prose further (information theory). If this context is headed to a "
                f"pricier model OR will be re-read across turns, consider --semantic "
                f"(the Haiku context-condenser): LOSSY but verified and fail-closed. "
                f"It NET-saves ONLY cross-model or on reuse; one-shot same-model use "
                f"is net-negative.")
    return ("modest",
            f"Tier 1 saved {pct:.1f}% (modest redundancy). A semantic tier is optional "
            f"and only worth it cross-model or on reuse (it is LOSSY and costs tokens).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reduce tokens by removing redundancy (rule-based, offline).")
    parser.add_argument("input", nargs="?", help="input file; omit to read stdin")
    parser.add_argument("-o", "--output", help="write result to a file instead of stdout")
    parser.add_argument("--stats", action="store_true", help="print before/after token stats to stderr")
    parser.add_argument("--auto", action="store_true",
                        help="advisory: after reducing, print an honest verdict on whether the "
                             "text had redundancy to exploit and whether --semantic is worth it")
    parser.add_argument("--json", action="store_true",
                        help="emit --stats/--auto output as JSON to stderr (for orchestration)")
    parser.add_argument("--level", default="balanced", choices=["safe", "balanced", "aggressive"],
                        help="reduction aggressiveness (default: balanced)")
    parser.add_argument("--similarity", type=float, default=None, help="override near-duplicate threshold (0-1)")
    parser.add_argument("--similarity-sent", type=float, default=None,
                        help="override near-duplicate SENTENCE threshold (0-1)")
    parser.add_argument("--no-whitespace", action="store_true")
    parser.add_argument("--no-dedup", action="store_true")
    parser.add_argument("--no-near-dedup", action="store_true")
    parser.add_argument("--no-sentence-dedup", action="store_true")
    parser.add_argument("--no-near-dedup-sentences", action="store_true",
                        help="disable sentence-level near-duplicate removal")
    parser.add_argument("--no-dedup-lists", action="store_true",
                        help="disable duplicate list-item removal")
    parser.add_argument("--no-compact-tables", action="store_true",
                        help="disable lossless Markdown table compaction")
    parser.add_argument("--strip-html-comments", action="store_true",
                        help="also remove <!-- ... --> from prose (opt-in; off by default)")
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
        if args.no_near_dedup_sentences:
            overrides["near_dedup_sentences"] = False
        if args.no_dedup_lists:
            overrides["dedup_lists"] = False
        if args.no_compact_tables:
            overrides["compact_tables"] = False
        if args.strip_html_comments:
            overrides["strip_html_comments"] = True
        if args.no_filler:
            overrides["filler"] = False
        if args.no_phrases:
            overrides["phrases"] = False
        if args.no_json:
            overrides["json"] = False
        if args.similarity is not None:
            overrides["similarity"] = args.similarity
        if args.similarity_sent is not None:
            overrides["similarity_sent"] = args.similarity_sent
        reduced = reduce_text(original, level=args.level, overrides=overrides,
                              tier2=args.tier2, tier2_char_threshold=args.tier2_chars)

    if args.stats or args.auto:
        sig = _redundancy_signals(original, reduced)
        payload = dict(sig)
        if args.auto:
            verdict, advice = _auto_verdict(sig)
            payload["verdict"] = verdict
            payload["advice"] = advice
        if args.json:
            sys.stderr.write(json.dumps(payload) + "\n")
        else:
            if args.stats:
                sys.stderr.write(_stats_line(sig, original, reduced) + "\n")
            if args.auto:
                sys.stderr.write(f"[auto] {payload['advice']}\n")
            elif (args.stats and sig["saved_pct"] < _AUTO_LOW_PCT
                  and sig["before_tokens"] >= _STATS_NOTE_MIN_TOK):
                # Honest low-savings note even without --auto (spec: never oversell).
                sys.stderr.write(
                    "[stats] This text is near its information floor; deterministic "
                    "reduction cannot shrink unique prose further without loss "
                    "(information theory). See --auto / --semantic.\n"
                )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(reduced + "\n")
    else:
        sys.stdout.write(reduced + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
