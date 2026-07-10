# Workflow routing for large tasks

Text compression (Tier 1/2) trims redundancy inside a given context. It does
**not** address the real cost driver of large tasks: loading a huge *raw*
context (a whole codebase, dozens of sources) into one expensive model call. The
lever there is **map-reduce routing** — cheap, fast subagents gather and condense
raw material in their own isolated context windows, and only their condensed
output flows into the capable model that does the actual reasoning/writing.

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
| Execute / write (`implementer`, `synthesizer`) | **sonnet** | The actual change or report, working from the condensed intermediate. |
| Hard architectural/analytical judgement | **opus**, only on demand | Reserve for genuinely ambiguous trade-offs or final quality review of a large change. Not a default — it is the expensive tier. |

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

## Measured routing numbers (real, from `examples/`)

Measured with the repo's own token counter (heuristic estimate — no `tiktoken`
in the test env; same method both sides so the ratio holds). These compare the
raw material a naive single call would load against the condensed intermediate
the routed approach feeds to the capable model.

| Case | Raw context (naive) | Condensed intermediate (routed) | Reduction |
|------|--------------------:|--------------------------------:|----------:|
| Refactor across 10 files (`06_refactor_*`) | 369 tok | 200 tok | **45.8%** |
| Research over 6 sources (`07_research_*`) | 716 tok | 270 tok | **62.3%** |

### Honest caveats
- **Not a full live benchmark.** These measure the structural token difference
  (raw vs. condensed) that *drives* the saving; they do not include the tokens
  the gathering subagents themselves spend. Treat them as the mechanism's lower
  bound, not a guaranteed end-to-end figure.
- **Task-dependent, no universal %.** The saving scales with how much raw
  material you avoid loading. The refactor fixtures are tiny (369 tok total), so
  45.8% *understates* real refactors: the scout map stays ~constant while a naive
  dump grows with file size, so on real hundred-line files the reduction is far
  larger. The research case (62.3%, real prose) is more representative.
- There is **no** blanket "80% saved" guarantee. Measure your own tasks against
  these examples before quoting a number.
