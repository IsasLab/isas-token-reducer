<div align="center">

# ISAS Token Reducer

### Humanity built powerful AI. Now we optimize its fuel.

**A Claude Skill that trims redundant tokens _before_ Claude reads them — private, safe, and measurable.**

[![CI](https://github.com/IsasLab/isas-token-reducer/actions/workflows/ci.yml/badge.svg)](https://github.com/IsasLab/isas-token-reducer/actions/workflows/ci.yml)
[![Reasoning-tested](https://img.shields.io/badge/reasoning-benchmarked%20on%20GSM8K-ff7a18.svg)](benchmarks/reasoning_preservation.py)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Zero dependencies](https://img.shields.io/badge/tier%201-zero%20dependencies-brightgreen.svg)](#whats-inside)

[**Live demo & landing page →**](https://isaslab.github.io/isas-token-reducer/) · [Install](#installation) · [How it works](#how-it-works) · [Benchmark](#credibility-measured-not-claimed)

</div>

---

Most token waste happens **before the model even starts thinking** — duplicated
paragraphs, filler phrases, and messy whitespace you pay full price to process.
ISAS strips that structural redundancy first, so every token you spend on Claude
earns its place. Tier 1 runs **100% offline** with zero dependencies and never
touches a number, quote, line of code, or contract clause.

## Proof, not promises

Real `--stats` output from running the tool on the fixtures in
[`examples/inputs/`](examples/inputs) — reproduce any row yourself:

```bash
python scripts/reduce.py examples/inputs/02_dup_paragraphs.txt --stats -o /dev/null
```

| Input | What it is | Tokens before → after | Saved |
|-------|------------|:---------------------:|:-----:|
| `01_long_chat.txt` | Long chat, repeated answers + filler | 347 → 217 | **37.5%** |
| `02_dup_paragraphs.txt` | Doc with duplicated paragraphs | 295 → 152 | **48.5%** |
| `05_mixed_context.txt` | Meeting notes: dupes + filler + whitespace | 178 → 98 | **44.9%** |
| `08_commented_module.js` | Heavily-commented JS (`--code` mode) | 448 → 138 | **69.2%** |

> **Honesty rule:** savings depend entirely on how redundant *your* input is —
> single digits on already-tight text, much higher on repetitive content. We
> never quote a fixed number; you measure your own with `--stats`. Token counts
> here use the labelled `words×1.3` fallback (install `tiktoken` for exact
> counts); the *percentage* is reliable because the same method is used on both
> sides.

## Credibility: measured, not claimed

Every compression tool claims it "preserves meaning." **We're the first in the
Claude-skill space to actually test it.**
[`benchmarks/reasoning_preservation.py`](benchmarks/reasoning_preservation.py)
is a paired **GSM8K** answer-stability benchmark: it runs the same math word
problems through Claude with and without reduction, at every level, and reports
whether the answers change — with Wilson confidence intervals, an exact McNemar
test, and honest sample-size/power math.

```bash
python benchmarks/reasoning_preservation.py --selftest    # offline, no API key
python benchmarks/reasoning_preservation.py --power-calc   # sample-size tables
```

It's deliberately honest about its own limits (GSM8K questions are short, so they
lightly exercise the tool; GSM8K may be memorized) — see the file's docstring.
That transparency *is* the credibility: numbers you can re-run, not a marketing
percentage.

**Safety is falsifiable, too.** [`tests/test_safety.py`](tests/test_safety.py)
proves — across all levels — that numbers, fenced/inline code, blockquotes, and
JSON payloads come out byte-intact. Near-duplicate removal is **number-aware**:
two paragraphs that read alike but carry a different figure are never collapsed,
so no fact is ever silently dropped.

## What's inside

```
isas-token-reducer/
├── SKILL.md                     # the skill definition Claude loads
├── install.sh                   # one-command installer for Claude Code
├── scripts/
│   ├── reduce.py                # core reduction engine (CLI + importable)
│   ├── reduce_code.py           # code-mode: comment/blank-line stripping
│   ├── count_tokens.py          # before/after token counting
│   └── classify_task.py         # small-vs-large task routing
├── benchmarks/
│   └── reasoning_preservation.py# GSM8K answer-stability benchmark
├── references/                  # techniques, phrase map, workflow routing
├── agents/                      # Claude Code subagents (workflow routing)
├── examples/                    # measured before/after cases + inputs
└── tests/                       # safety + smoke suite (CI-enforced)
```

- **Tier 1 (always on, offline, zero deps):** whitespace normalization, exact &
  near-duplicate removal, sentence dedup, filler-phrase trimming, verbose-phrase
  compression, lossless JSON minify. Python standard library only.
- **Tier 2 (optional, opt-in):** summarize long blocks via the Claude API — only
  when you pass `--tier2` and set `ANTHROPIC_API_KEY`.
- **Workflow routing (Claude Code):** for large refactors and multi-source
  research, cheap gathering subagents condense raw material before an expensive
  model reasons over it — the real token lever for big tasks.

## Quick use

```bash
python scripts/reduce.py yourfile.txt --stats          # reduced text → stdout, stats → stderr
python scripts/reduce.py yourfile.txt -o out.txt       # write to a file
python scripts/reduce.py yourfile.txt --level safe     # safe | balanced (default) | aggressive
python scripts/reduce.py app.py --code --stats         # code-context mode (never edits your file)
python scripts/classify_task.py brief.txt --files 12   # small or large task?
```

Install `tiktoken` for exact token counts; without it, counts are clearly
labelled estimates.

## Installation

### Claude Code (recommended — one command)
```bash
curl -fsSL https://raw.githubusercontent.com/IsasLab/isas-token-reducer/main/install.sh | bash
```
Installs the skill + workflow-routing subagents automatically. No restart needed.

Without `curl` (e.g. a corporate proxy) — clone manually:
```bash
git clone https://github.com/IsasLab/isas-token-reducer ~/.claude/skills/isas-token-reducer \
  && cp isas-token-reducer/agents/*.md ~/.claude/agents/
```

**Windows:** run the command in **Git Bash** or **WSL** (not cmd.exe / PowerShell).
Git Bash is present on virtually every Windows dev machine, so there is
deliberately no separate `.ps1` in v1.

### Claude.ai (Chat)
1. Download the ready-made ZIP from the GitHub Releases page (don't zip it
   yourself — a wrong folder structure is the most common reason an uploaded
   skill never triggers). Or build it locally with
   `bash scripts/build_claude_ai_zip.sh`.
2. Settings → Capabilities → Skills → **+ Create skill** → upload the ZIP.
3. Enable the skill (toggle).

> **Note:** automatic per-step model routing via subagents is a **Claude Code**
> feature. On Claude.ai the skill supplies the same staged strategy as guidance;
> the token benefit there comes from the structure (condense first, then
> process), not from cheaper per-step models. Skills are managed separately per
> surface — an upload to Claude.ai doesn't cover Claude Code or the API.

## How it works

1. **Point it at your text or task** — a document, prompt, or job headed to Claude.
2. **It trims the redundancy** — duplicates, filler, and whitespace removed
   safely; large tasks are routed through condensing subagents instead.
3. **Feed the leaner context to Claude** — fewer, denser tokens; the model
   spends its budget on reasoning, not repetition.

See [`SKILL.md`](SKILL.md) for the full workflow and safety rules, and
[`examples/before_after.md`](examples/before_after.md) for every measured case.

## Contributing

Small, focused PRs welcome. Any change to the reduction core needs a passing
safety test proving numbers/code/quotes are untouched — see
[`CONTRIBUTING.md`](CONTRIBUTING.md). CI runs the safety suite and the benchmark
self-test on Python 3.9–3.12.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — free for noncommercial use.
