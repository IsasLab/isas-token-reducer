---
name: isas-token-reducer
description: Reduce tokens before Claude processes text: strip duplicate, near-duplicate, whitespace, and filler content. Use for long chats, big pasted docs, or "reduce context / save tokens" requests.
---

# ISAS Token Reducer

## Overview
Compresses context and prompts **before** they are processed, cutting token
cost. Two tiers, plus routing:

- **Tier 1 — deterministic (default, free, offline, byte-safe).** Python
  standard library only: whitespace normalization, exact/near-duplicate removal,
  filler-phrase trimming, lossless JSON minify. It removes only *structural
  redundancy* and **never changes a number, quote, line of code, name, or legal
  clause**. On genuinely unique prose it can only save a little — that is
  information theory, not a bug (see the honest note in `--stats`).
- **Tier 2 — semantic (opt-in, LOSSY, costs tokens).** Rewrites/densifies unique
  prose that Tier 1 cannot shrink. The **primary** path on Claude Code is the
  skill-orchestrated `context-condenser` subagent (haiku, no API key, no pip
  dep): a cheap model reads the raw material in an isolated context and returns
  only a dense, fidelity-checked digest, so the raw dump never enters the
  expensive model's window. It is **net-positive only** when a cheap model
  condenses for a pricier one, or the digest is reused across turns — see
  **When NOT to run the semantic tier** below. (A programmatic SDK fallback,
  `reduce.py --tier2`, is the secondary path for pipelines outside Claude Code.)

For large tasks it also routes work through a **map-reduce** workflow so cheap
gathering agents condense raw material before an expensive model reasons over
it. Use `reduce.py --auto` to get an honest verdict on which tier (if any) is
worth running.

## When to apply
Apply when any of these hold:
- A long conversation or a large document was pasted in.
- The same or nearly-identical content repeats across the context.
- The user says "shorten", "reduce context", "save tokens", "trim this", or
  similar.
- You are about to feed a big raw dump (whole codebase, many sources) into a
  single expensive call — see **Workflow routing** below.

## Workflow (text compression)
0. **When unsure which tier to run, ask the advisor first:**
   ```
   python scripts/reduce.py input.txt --auto
   ```
   It runs the free Tier 1 pass, writes the reduced text to stdout
   (non-destructive), and prints ONE honest verdict line to stderr: whether
   Tier 1 already handled it, whether the text is near its information-theory
   floor (send as-is), or whether the semantic tier is worth it — and it
   **refuses** the semantic tier by default when it would be net-negative
   (one-shot, same model). The advisor never runs the lossy tier itself.
1. To run Tier 1 directly, write the text to a file (or pipe it) and run:
   ```
   python scripts/reduce.py input.txt --stats
   ```
   `--stats` prints before/after token counts and % saved to stderr; the reduced
   text goes to stdout (or use `-o output.txt`). When savings are low on a
   non-trivial input, `--stats` also prints the honest note that unique prose
   cannot be shrunk further without loss.
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

## Semantic tier (Tier 2) — LOSSY, opt-in, densifies unique prose
Tier 1 cannot shrink prose that has no redundancy. When the material is large,
genuinely unique, and headed for an expensive model, the **semantic tier** can
rewrite it denser — but it is **lossy** and it **costs tokens to run**, so use
it only when the economics work (next section).

**Order is fixed: deterministic pre-pass → THEN semantic densify.** Always run
Tier 1 first (`reduce.py`) to strip structural redundancy for free, then feed
that Tier-1 output to the semantic tier. Never densify first — doing so can fuse
two distinct facts before dedup can compare them, and wastes the cheap model on
redundancy Tier 1 removes for nothing.

**Primary path (Claude Code): the `context-condenser` subagent** — haiku,
read-only, no API key, no pip dependency. It is the general prose densifier the
agent set otherwise lacks (`context-scout` is refactor-only, `research-gatherer`
is web-only). Spawn it in its own isolated context to read the (Tier-1-reduced)
raw material; it returns only a dense, **fidelity-checked** digest. The expensive
main model ingests only that digest — the raw dump never enters its window.

1. Run Tier 1 and confirm the tier is worth it: `reduce.py input.txt --auto`.
2. Spawn `context-condenser` on the Tier-1 output; take back the digest only.
3. **Verify before use (fail closed):** the condenser self-checks, and you can
   confirm offline with `python scripts/semantic.py --verify <source> <digest>`
   (numbers order-aware; code/quotes/proper-nouns by presence). If any required
   span is missing, discard the digest and use the Tier-1 text unchanged.
4. State plainly that the digest is a **lossy** working copy, not the user's
   source. Keep the cut-manifest / verification in the side channel only — never
   feed it into the expensive model's context (it erodes the saving).

**Secondary path (outside Claude Code): `reduce.py --tier2`** — a programmatic
SDK fallback that summarizes long paragraphs via the Claude API. It needs the
`anthropic` SDK and `ANTHROPIC_API_KEY`; if either is missing it skips cleanly
and returns Tier 1. Prefer the subagent on Claude Code.

### When NOT to run the semantic tier (net-negative — read this)
The semantic tier reads the input once to shrink it, which **always costs
tokens**. It only NET-saves when ALL of these hold:
- a genuinely **cheaper** model condenses than the model that consumes the
  result (haiku → sonnet/opus), **OR** the digest is **reused across ≥ 2**
  downstream turns/calls so the one-time cost amortizes;
- the raw context is large enough that the digest is **much smaller** than raw
  (rule of thumb: digest ≤ ~40% of raw);
- it is **not** a one-shot, same-model, single-pass use.

**A one-shot, same-model, single-pass use is ALWAYS net-negative** — the
expensive model would read the raw context once anyway, so paying a second read
to shrink it only loses tokens. `--auto` refuses this case by default; don't
override it without a real reuse or cross-model reason.

**Hard exclusions — never semantically condense these at all** (not "verify",
*block*): legal/contract text, verbatim quotations, and exact-wording specs.
Carry them through verbatim; densify only the surrounding connective prose.

**Platform honesty:** the cheap-model arbitrage exists only on Claude Code,
where a subagent runs on haiku in a separate context. On Claude.ai one model
serves the whole chat — there is no per-step routing, so the *price* lever is
gone and only the **reuse** lever remains. Do not claim the cross-model saving
there.

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
     - General prose (not a refactor, not web sources): `context-condenser`
       (haiku, read-only) densifies a large, unique block → the expensive main
       model reasons over only the digest. **LOSSY**: run Tier 1 first, verify
       fidelity, and only when net-positive (cross-model or reused) — see
       **Semantic tier** above.
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
- `agents/context-condenser.md` — the haiku prose-densifier subagent that backs
  the semantic tier (LOSSY, fidelity-checked, fail-closed).
