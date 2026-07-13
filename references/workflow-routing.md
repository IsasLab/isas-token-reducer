# Workflow routing for large tasks

Text compression shrinks a *given* context two ways: **Tier 1** (deterministic)
removes structural redundancy losslessly; **Tier 2** (semantic) rewrites unique
prose denser but is **lossy and costs tokens to run**. Neither, by itself,
addresses the real cost driver of large tasks: loading a huge *raw* context (a
whole codebase, dozens of sources) into one expensive model call. The lever
there is **map-reduce routing** — cheap, fast subagents gather and condense raw
material in their own isolated context windows, and only their condensed output
flows into the capable model that does the actual reasoning/writing.

## Decision logic (`scripts/classify_task.py`)

A task is **large** if any signal crosses its threshold (all configurable):

| Signal | Default threshold | Flag |
|--------|-------------------|------|
| Estimated raw-context tokens (if naively stuffed into one call) | > 15,000 | `--token-threshold` |
| Files a refactor touches | > 8 | `--file-threshold` |
| Sources/searches a research task needs | > 5 | `--source-threshold` |

- **small** → single-pass: handle directly; optionally run `reduce.py` on any
  pasted context.
- **large** → workflow routing: condense first, then process the condensed
  result.

The classifier also guesses the *kind* (refactor vs research) to suggest which
agents to use.

## Model mapping

| Role | Model | Why |
|------|-------|-----|
| Gather / scan / triage (`context-scout`, `research-gatherer`) | **haiku** | High-volume, low-judgement reading. Cheap and fast; runs in an isolated context so raw material never touches the main window. |
| Densify unique prose (`context-condenser`) | **haiku** | The semantic tier's cheap side. Reads a large, unique block and returns a lossy, fidelity-checked digest. Net-positive **only** cross-model (haiku → sonnet/opus) or when the digest is reused across ≥ 2 turns; refuse one-shot same-model use. Always run Tier 1 (`reduce.py`) first, then condense. |
| Execute / write (`implementer`, `synthesizer`) | **sonnet** | The actual change or report, working from the condensed intermediate. |
| Hard architectural/analytical judgement | **opus**, only on demand | Reserve for genuinely ambiguous trade-offs or final quality review of a large change. Not a default — it is the expensive tier. |

## Semantic tier in routing (order + economics)

The `context-condenser` subagent is the general-prose leg of routing: when the
raw material is large and genuinely **unique** (Tier 1 can't shrink it) and it is
headed for an expensive model, condense it on haiku first.

**Fixed order: deterministic pre-pass → THEN semantic densify.**

1. Run Tier 1 (`reduce.py`) on the raw material — free, byte-safe, removes
   duplicates/filler/whitespace.
2. Feed that Tier-1 output to `context-condenser` (haiku, isolated context). It
   returns only a lossy, fidelity-checked digest.
3. The expensive model reasons over the digest only; verify fidelity first
   (`scripts/semantic.py --verify`) and fail closed to the Tier-1 text if any
   number/name/quote/code span is missing.

Never densify before deduping — trimming first can fuse two originally-distinct
passages into false-identical text (silent fact loss) and spends the cheap model
on redundancy Tier 1 would have removed for free.

**Net-save rule (same as routing above):** condensing always costs one read, so
it net-saves **only** cross-model (haiku → sonnet/opus) OR when the digest is
reused across ≥ 2 turns. A **one-shot, same-model, single-pass** condense is
**always net-negative** — the expensive model would read the raw once anyway;
`reduce.py --auto` refuses it by default. Also hard-block legal/contract text,
verbatim quotes, and exact-wording specs from semantic condensing entirely.

## Platform difference (read this — it is not the same everywhere)

**Automatic per-step model routing via subagents is a Claude Code feature only.**
It relies on the `agents/*.md` files installed under `~/.claude/agents/` (or a
project `.claude/agents/`). Claude picks the subagent automatically from its
`description`, runs it in an isolated context, and each subagent uses its own
declared model.

**Claude.ai (chat) has no subagent system.** The model is chosen once for the
whole conversation; you cannot force haiku for gathering and sonnet for writing
within one chat. There, this skill can only supply the *strategy* as guidance:

1. **Gather/condense phase** — pull the raw material and write compact
   intermediate notes (paths + line ranges for refactors; a few key sentences
   per source for research). Keep raw dumps out of the running context.
2. **Synthesis phase** — do the real work from those condensed notes.

On Claude.ai the saving comes from the **structure** (condense, then process),
not from cheaper per-step models. Do not claim the model-routing saving there.
The same applies to the semantic tier: with one model for the whole chat there
is no haiku-for-condensing price lever, so its only remaining justification is
**reuse** (condense once, re-read the digest across many turns). A single-pass
condense on Claude.ai is net-negative — skip it.

## Measured routing numbers (real, from `examples/`)

**Read these caveats before you read the numbers — they decide whether the
numbers apply to your task at all.**

- **Net-benefit precondition.** Routing (and the semantic tier) only NET-saves
  when a cheaper model does the gathering/condensing than the model that
  consumes the result (**cross-model routing**), OR the condensed intermediate
  is **reused across ≥ 2 downstream turns/calls**. A one-shot, same-model pass
  gains nothing from routing — the reductions below are the *structural*
  difference, not a free win.
- **Excludes gathering tokens.** These measure only the structural token
  difference (raw vs. condensed) that *drives* the saving; they do **not**
  include the tokens the gathering/condensing subagents themselves spend. Treat
  them as the mechanism's lower bound, not a guaranteed end-to-end figure.
- **Refactor number understates.** The refactor fixtures are tiny (369 tok
  total), so 45.8% *understates* real refactors: the scout map stays ~constant
  while a naive dump grows with file size, so on real hundred-line files the
  reduction is far larger. The research case (62.3%, real prose) is more
  representative.
- **Different mechanism from Tier 1 — not transplantable.** These routing/
  semantic percentages measure *avoiding a raw load* across models/turns; the
  Tier-1 percentages elsewhere measure *removing structural redundancy* in one
  text. They are not the same lever and must **never** be quoted onto a small,
  single-model task. There is **no** blanket "80% saved" guarantee — measure
  your own task before quoting any number.

Measured with the repo's own token counter (heuristic estimate — no `tiktoken`
in the test env; same method both sides so the ratio holds). These compare the
raw material a naive single call would load against the condensed intermediate
the routed approach feeds to the capable model.

| Case | Raw context (naive) | Condensed intermediate (routed) | Reduction |
|------|--------------------:|--------------------------------:|----------:|
| Refactor across 10 files (`06_refactor_*`) | 369 tok | 200 tok | **45.8%** |
| Research over 6 sources (`07_research_*`) | 716 tok | 270 tok | **62.3%** |
