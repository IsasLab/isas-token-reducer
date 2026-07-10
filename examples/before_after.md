# Before / after — measured

All numbers below are **real** outputs from running the tools in this repo on the
input files in `examples/inputs/`, not estimates hand-written for illustration.

**Measurement note:** the test environment did not have `tiktoken` installed, so
token counts come from the labelled fallback heuristic (`words * 1.3`). They are
**estimates**, consistent across before/after (same method both sides), so the
*percentage saved* is reliable even though the absolute token count is
approximate. Install `tiktoken` for exact counts.

Reproduce any row with:
```
python scripts/reduce.py examples/inputs/<file> --stats -o /dev/null
```

## Part A — text compression (Tier 1)

| # | Case | File | Tokens before | Tokens after | Saved |
|---|------|------|--------------:|-------------:|------:|
| 1 | Long chat with repeated answers + filler | `01_long_chat.txt` | 347 | 217 | **37.5%** |
| 2 | Doc with duplicated paragraphs | `02_dup_paragraphs.txt` | 295 | 152 | **48.5%** |
| 3 | Prompt full of filler phrases | `03_filler_prompt.txt` | 151 | 108 | **28.5%** |
| 4 | Near-duplicate paragraphs (0.9 threshold) | `04_near_dup.txt` | 192 | 152 | **20.8%** |
| 5 | Mixed meeting notes (dupes + filler + whitespace) | `05_mixed_context.txt` | 178 | 98 | **44.9%** |

Tier-1 compression on these five cases: **~21%–49% saved**, averaging ~36%. The
saving depends entirely on how much redundancy the input actually contains — a
already-tight text will save little, and that is correct behaviour.

### Safety spot-checks (verified on the reduced output)
- Case 2: every figure survived (`5`/hr, `20`/day, backoff `1s,2s,4s,8s`, `4`
  attempts, `30` days). All four *distinct* paragraphs kept; only the three
  verbatim repeats of the intro paragraph were collapsed to one.
- Case 4: near-duplicate removal at 0.9 was **conservative** — it dropped one
  paragraph that was a near-verbatim restatement, but kept the two email
  paragraphs and the reordered migration paragraph because they scored below
  0.9. This is the intended safe default (raise `--similarity` for even less
  aggressive merging).

## Part B — workflow routing (large tasks)

For large tasks the lever is **how many tokens reach the expensive model**, not
Tier-1 compression. These rows measure the token volume of the raw material a
naive single-call approach would load, versus the condensed intermediate that
the routed approach feeds to the executing/synthesizing model. Same token
counter both sides.

**Honest scope:** these measure the *structural* token difference (raw vs.
condensed intermediate) — the mechanism behind routing savings — on
representative fixtures. They are **not** a full live multi-agent benchmark, and
the subagents themselves spend some tokens gathering. Treat them as a lower bound
on the mechanism, not a guaranteed end-to-end percentage.

| Case | Files/Sources | Raw context (naive) | Condensed intermediate (routed) | Reduction in tokens reaching the big model |
|------|--------------|--------------------:|--------------------------------:|-------------------------------------------:|
| Refactor: `format_price` across 10 files | 12 files | 369 | 200 (`06_refactor_scout_map.txt`) | **45.8%** |
| Research: CDN cache invalidation, 6 sources | 6 sources | 716 | 270 (`07_research_digest.txt`) | **62.3%** |

Both are classified `LARGE` by `classify_task.py`:
```
python scripts/classify_task.py examples/inputs/06_refactor_raw_dump.txt --files 10
  -> LARGE (refactor) -> context-scout, implementer
python scripts/classify_task.py examples/inputs/07_research_raw_sources.txt --sources 6
  -> LARGE (research) -> research-gatherer, synthesizer
```

**Important caveat on the refactor number:** the 10 fixture files are tiny
(369 tokens total), so 45.8% understates reality. The scout map stays roughly
constant in size regardless of how big each file is, while a naive dump grows
with file size — so on a real refactor where each file is hundreds of lines, the
raw dump is far larger while the map barely changes, and the reduction is much
higher. The research case (real-prose sources) at 62.3% is more representative.

See `references/workflow-routing.md` for the decision logic and model mapping.
