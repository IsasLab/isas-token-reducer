# isas-token-reducer
Humanity built powerful AI. Now we optimize its fuel.

A Claude Skill that **reduces tokens before Claude processes your text** — so
long conversations, big pasted documents, and repetitive context cost less
without changing meaning.

- **Tier 1 (always on, offline, zero dependencies):** whitespace normalization,
  exact & near-duplicate removal, filler-phrase trimming. Python standard
  library only — no API key, no network, no install beyond copying the folder.
- **Tier 2 (optional):** summarize long blocks via the Claude API — only when you
  opt in with `--tier2` and set `ANTHROPIC_API_KEY`.
- **Workflow routing (Claude Code):** for large refactors and multi-source
  research, cheap gathering subagents condense raw material before an expensive
  model reasons over it — the real token lever for big tasks.

Safety first: the reducer only removes **structural** redundancy. It never
changes numbers, quotes, code, names, or legal wording. When unsure, it keeps
the original.

## What's inside

```
isas-token-reducer/
├── SKILL.md                     # the skill definition Claude loads
├── install.sh                   # one-command installer for Claude Code
├── scripts/
│   ├── reduce.py                # core reduction engine (CLI + importable)
│   ├── count_tokens.py          # before/after token counting
│   ├── classify_task.py         # small vs large task routing
│   └── build_claude_ai_zip.sh   # builds the Claude.ai upload ZIP
├── references/
│   ├── techniques.md            # each technique + when NOT to apply + filler list
│   └── workflow-routing.md      # routing logic, model mapping, measured numbers
├── agents/                      # Claude Code subagents (workflow routing)
│   ├── context-scout.md · research-gatherer.md · implementer.md · synthesizer.md
└── examples/
    └── before_after.md          # measured before/after cases
```

## Quick use

```
python scripts/reduce.py yourfile.txt --stats        # reduced text to stdout, stats to stderr
python scripts/reduce.py yourfile.txt -o out.txt     # write to a file
python scripts/classify_task.py brief.txt --files 12 # small or large?
```

Install `tiktoken` for exact token counts; without it, counts are clearly
labelled estimates.

## Installation

### Claude Code (recommended — one command)
```
curl -fsSL https://raw.githubusercontent.com/IsasLab/isas-token-reducer/main/install.sh | bash
```
Installs the skill + workflow-routing subagents automatically. No restart needed.

Alternative without curl (e.g. a corporate proxy blocks downloads) — clone
manually:
```
git clone https://github.com/IsasLab/isas-token-reducer ~/.claude/skills/isas-token-reducer \
  && cp isas-token-reducer/agents/*.md ~/.claude/agents/
```

**Windows:** run the `curl`/`git` command above in **Git Bash** or **WSL** (not
cmd.exe / PowerShell). There is deliberately no separate `.ps1` in v1 — Git Bash
is present on virtually every Windows dev machine.

### Claude.ai (Chat)
1. Download the ready-made ZIP from the GitHub Releases page (don't zip it
   yourself — a wrong folder structure is the most common reason an uploaded
   skill never triggers). Or build it locally: `bash scripts/build_claude_ai_zip.sh`
   produces `isas-token-reducer.zip` with the correct structure.
2. Settings > Capabilities > Skills > "+ Create skill" > upload the ZIP.
3. Enable the skill in the list (toggle).

**Automatic per-step model routing via subagents does not exist on Claude.ai**
(there is no `agents/` mechanism; the model is chosen once per conversation).
There the skill only supplies the staged approach as guidance — the token
benefit comes from the structure (condense first, then process), not from
cheaper per-step models.

> **Note:** Skills are managed separately per surface — an upload to Claude.ai
> does not automatically cover Claude Code or the API, and vice versa.

## License

PolyForm Noncommercial License 1.0.0 — see [LICENSE](LICENSE). Free for
noncommercial use.
