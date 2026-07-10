# Reduction techniques

This document is the reference for every reduction technique in
`scripts/reduce.py`, and it is the **maintained source of the filler-phrase
list** (`reduce.py` parses the list out of the marked block below — edit it
here, not in the script).

Golden rule for every technique: **only structural redundancy is removed.**
Numbers, quotes, code, names, and legal/contractual wording are never rewritten.
When in doubt, keep the original.

---

## Tier 1 — offline, standard library only

### 1. Whitespace normalization
`normalize_whitespace()`

- Converts tabs to spaces, strips trailing whitespace on each line, and
  collapses 3+ consecutive newlines down to a single blank line.
- **When NOT to apply:** whitespace-significant formats (Python source,
  Makefiles, YAML, Markdown code fences, ASCII tables/diagrams). For prose it is
  safe; for code, pass `--no-whitespace` or don't run the reducer at all.

### 2. Exact duplicate removal
`remove_exact_duplicates()`

- Operates at **paragraph** granularity. A paragraph is dropped only if its
  whitespace/case-normalized text has already appeared verbatim earlier.
- Never merges or rewrites — a kept paragraph is byte-for-byte the original.
- **When NOT to apply:** content where an intentional repeat carries meaning
  (a refrain, a repeated legal clause per section, test fixtures asserting
  duplication). Use `--no-dedup`.

### 3. Near-duplicate removal
`remove_near_duplicates()` — `difflib.SequenceMatcher`, no ML model, no network.

- Drops a paragraph when its similarity ratio to an already-kept paragraph is
  `>= threshold` (default **0.9**, tune with `--similarity`).
- Deterministic and explainable; there is no embedding model or API involved.
- **When NOT to apply:** near-identical items that are meaningfully distinct —
  API examples that differ by one parameter, rows of data, near-duplicate legal
  clauses with a critical word changed. Lower the risk by raising the threshold
  (e.g. `--similarity 0.97`) or disabling with `--no-near-dedup`.
- **Caution:** at low thresholds two paragraphs that differ only in a number or
  a negation ("must" vs "must not") can look similar. Keep the threshold high
  (>= 0.9) for anything factual.

### 4. Filler-phrase trimming
`trim_filler()`

- Removes low-information connector phrases (see list below), then tidies the
  spacing left behind. It removes phrases; it does not paraphrase.
- **When NOT to apply:** quotes and verbatim excerpts (you'd alter a quotation),
  legal text, or any passage where the phrase is inside a term of art. Use
  `--no-filler`.

The maintained filler list (one phrase per line, case-insensitive). Add or
remove phrases here — `reduce.py` reads exactly this block:

<!-- FILLER-LIST-START -->
it is important to note that
it should be noted that
it is worth noting that
please note that
as previously mentioned
as mentioned above
as already stated
as a matter of fact
it goes without saying that
the fact of the matter is that
needless to say
at the end of the day
when all is said and done
for all intents and purposes
in the final analysis
in conclusion
to summarize
to sum up
basically
essentially
actually
<!-- FILLER-LIST-END -->

---

## Tier 2 — optional, network (opt-in)

`_tier2_summarize()` runs **only** when `--tier2` is passed **and**
`ANTHROPIC_API_KEY` is set. It summarizes paragraphs longer than a configurable
character threshold via the Claude API, instructed to preserve every number,
name, quote, and factual claim exactly.

- The `anthropic` SDK is **lazy-imported**, so Tier 1 never needs it or the
  network. If the key is unset or the SDK is missing, Tier 2 is skipped cleanly
  and Tier 1 output is returned.
- **When NOT to apply:** any material where exact wording matters (contracts,
  quotes, specs). Tier 2 paraphrases; Tier 1 does not. Prefer Tier 1 for
  high-fidelity content.

---

## What the reducer must never do

- Change a number, date, amount, or unit.
- Alter a quotation or verbatim excerpt.
- Touch source code semantics.
- Reword legal or contractual language.
- Drop a paragraph that only *looks* redundant but carries a distinguishing
  detail (a negation, a different figure).

If any of these are in scope, run with the relevant technique disabled, or skip
reduction and ask the user first. See `SKILL.md` → "When NOT to reduce".
