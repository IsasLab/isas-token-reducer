---
name: isas-token-reducer
description: Reduce tokens before Claude processes text: strip duplicate, near-duplicate, whitespace, and filler content. Use for long chats, big pasted docs, or "reduce context / save tokens" requests.
---

# ISAS Token Reducer

## Overview
Compresses context and prompts **before** they are processed, cutting token
cost without changing meaning. Tier 1 is rule-based and fully offline (Python
standard library only): whitespace normalization, exact/near-duplicate removal,
and filler-phrase trimming. For large tasks it also routes work through a
map-reduce workflow so cheap gathering agents condense raw material before an
expensive model reasons over it.

## When to apply
Apply when any of these hold:
- A long conversation or a large document was pasted in.
- The same or nearly-identical content repeats across the context.
- The user says "shorten", "reduce context", "save tokens", "trim this", or
  similar.
- You are about to feed a big raw dump (whole codebase, many sources) into a
  single expensive call — see **Workflow routing** below.

## Workflow (text compression)
1. Write the text to a file (or pipe it) and run:
   ```
   python scripts/reduce.py input.txt --stats
   ```
   `--stats` prints before/after token counts and % saved to stderr; the reduced
   text goes to stdout (or use `-o output.txt`).
2. Pick aggressiveness with `--level safe|balanced|aggressive` (default
   `balanced`). Techniques by level: whitespace, lossless JSON minify, filler
   trimming, exact/near-duplicate removal (all levels); verbose-phrase
   compression, sentence-level dedup, markdown normalization (`balanced`+);
   lower near-dup threshold + blank-line removal (`aggressive`). See
   `references/techniques.md`.
3. Tune individually when needed: `--similarity 0.97`, `--no-phrases`,
   `--no-filler`, `--no-near-dedup`, `--no-sentence-dedup`, `--no-json`,
   `--no-whitespace`.
4. Use the reduced text in place of the original. Report the measured savings.

### When NOT to reduce (safety — read this)
**The reducer only removes structural redundancy (repeats, whitespace, filler).
It must never change numbers, quotes, code, names, or legal/contractual
wording.** Do NOT auto-reduce, and ask the user first, when the content is:
- source code, config, or anything whitespace-significant → `--no-whitespace`
  or skip;
- quotations, verbatim excerpts, or legal/contract text;
- data where near-duplicate rows or clauses differ by a critical word/number.

When unsure, keep the original text. Prefer conservative settings (high
`--similarity`, disable filler) over losing a distinction.

## Reducing code (explicit, opt-in)
The default reducer above must NOT touch code. Code has its own mode that
operates on a **copy you feed as context** — it never rewrites the user's real
files:
```
python scripts/reduce.py app.py --code --stats      # or: reduce_code.py app.py --stats
```
It removes comments, blank-line runs, and trailing whitespace, and is
language-aware (Python via `tokenize`/`ast`; C-family and hash-comment scanners
are string-safe). It **preserves** shebangs, encoding lines, and directive
comments (`noqa`, `type:`, `eslint-disable`, `@ts-ignore`, `go:build`, SPDX,
etc.), and never alters strings, numbers, or logic.

Savings are comment-density dependent. Extra levers (see `reduce_code.py`):
`--strip-docstrings` (Python) and `--skeleton` (keep signatures, drop bodies —
biggest cut, structure only). Use code mode when passing source as *context*;
do not present the stripped copy as the user's file.

## Workflow routing for large tasks
Text compression alone is not the main lever for big tasks (large refactors,
multi-source research). The real saving comes from **not** loading the whole raw
context into one expensive model. Instead:

1. Classify the task:
   ```
   python scripts/classify_task.py brief.txt --files 12 --sources 7
   ```
   "large" ≈ > 15,000 raw tokens, or > 8 files, or > 5 sources (configurable).
2. If **large**, route as map-reduce:
   - **On Claude Code** — use the installed subagents. Run the gatherers first,
     then feed only their condensed output to the executor/synthesizer:
     - Refactor: `context-scout` (haiku, read-only) maps files/line-ranges →
       `implementer` (sonnet) edits from that map, not a full dump.
     - Research: `research-gatherer` (haiku) condenses each source →
       `synthesizer` (sonnet; opus only for genuinely ambiguous synthesis)
       writes the report.
   - **On Claude.ai (chat)** — there is **no** subagent/per-step model routing.
     Follow the same *strategy* manually: a gather-and-condense phase producing
     compact intermediate notes, then a synthesis phase over those notes. The
     saving comes from the structure (condense, then process), not from cheaper
     per-step models. See `references/workflow-routing.md`.

## Examples
See `examples/before_after.md` for measured cases. Two quick ones:
- A long chat with repeated Q&A and filler: duplicate turns and connector
  phrases removed, meaning intact.
- A doc with copy-pasted paragraphs: exact + near-duplicate paragraphs collapsed
  to one, numbers untouched.

## Reference
- `references/techniques.md` — every technique, when NOT to apply it, and the
  maintained filler list.
- `references/workflow-routing.md` — classification logic, model mapping, the
  Claude Code vs Claude.ai difference, and the real measured routing numbers.
