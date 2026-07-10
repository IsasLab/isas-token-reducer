# ISAS Token Reducer

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

### Claude Code (empfohlen — ein Kommando)
```
curl -fsSL https://raw.githubusercontent.com/IsasLab/isas-token-reducer/main/install.sh | bash
```
Installiert Skill + Workflow-Routing-Subagenten automatisch. Kein Neustart nötig.

Alternative ohne curl (z. B. Firmen-Proxy blockt Downloads) — manuell klonen:
```
git clone https://github.com/IsasLab/isas-token-reducer ~/.claude/skills/isas-token-reducer \
  && cp isas-token-reducer/agents/*.md ~/.claude/agents/
```

**Windows:** Führe das obige `curl`/`git`-Kommando in **Git Bash** oder **WSL**
aus (nicht in cmd.exe / PowerShell). Ein separates `.ps1` gibt es in v1 bewusst
nicht — Git Bash ist auf Windows-Entwicklermaschinen praktisch immer vorhanden.

### Claude.ai (Chat)
1. Fertige ZIP von der GitHub-Releases-Seite laden (nicht selbst zippen —
   Struktur-Fehler sind der häufigste Grund, warum ein Skill nach Upload nicht
   triggert). Alternativ lokal bauen: `bash scripts/build_claude_ai_zip.sh`
   erzeugt `isas-token-reducer.zip` mit korrekter Ordnerstruktur.
2. Settings > Capabilities > Skills > "+ Create skill" > ZIP hochladen.
3. Skill in der Liste aktivieren (Toggle).

**Automatisches Modell-Routing über Subagenten gibt es auf Claude.ai nicht**
(kein `agents/`-Mechanismus; das Modell wird pro Konversation manuell gewählt).
Der Skill gibt dort nur die gestufte Vorgehensweise als Anleitung vor — der
Tokenvorteil kommt aus der Struktur (erst verdichten, dann verarbeiten), nicht
aus günstigeren Modellen pro Schritt.

> **Hinweis:** Skills werden pro Oberfläche separat verwaltet — ein Upload auf
> Claude.ai deckt nicht automatisch Claude Code oder die API ab, und umgekehrt.

## License

PolyForm Noncommercial License 1.0.0 — see [LICENSE](LICENSE). Free for
noncommercial use.
