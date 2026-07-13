# Marketing kit — ISAS Token Reducer

Ready-to-use copy for the repo, launch posts, and the landing page.
The landing page lives in [`docs/token-reducer/index.html`](docs/token-reducer/index.html),
linked from the IsasLab hub at [`docs/index.html`](docs/index.html) (enable GitHub
Pages → "Deploy from branch" → `/docs`).

**Honesty rules for all copy here:**
1. **Never quote a fixed savings percentage.** Real savings are input-dependent —
   ~0–1% on unique, already-tight prose (information theory: a lossless pass can't
   shrink text that has no redundancy), higher on repetitive content, largest via
   workflow routing on big tasks. Always point to `--stats` for real measurement.
2. **Never imply the semantic tier is "free savings."** It is *lossy* and *costs
   tokens to run*. It only *nets* a saving when a cheaper model condenses for a
   pricier one, or when the condensed artifact is reused across turns. One-shot,
   same-model use is net-negative — copy must say so, never gloss over it.

---

## Taglines

1. Humanity built powerful AI. Now we optimize its fuel. *(primary)*
2. Cleaner input in. Same answers out. Fewer tokens spent.
3. Trim the redundancy before Claude ever reads it.
4. Refine the fuel, not the engine.
5. Less waste in the prompt. More runway in the budget.

## Elevator pitch

ISAS Token Reducer is a Claude Skill that cleans redundant, repetitive, and
bloated text *before* Claude processes it — so you stop paying tokens for content
that adds nothing. Tier 1 runs 100% offline with zero dependencies and never
touches your numbers, quotes, code, or contract wording. For big jobs in Claude
Code, it routes work map-reduce style so cheap subagents do the heavy gathering
and only the condensed result reaches your capable model.

## Hero

- **Headline:** Optimize the fuel before it burns.
- **Subhead:** ISAS Token Reducer strips structural redundancy from your input —
  offline, safe, and measurable — so every token you spend on Claude earns its place.
- **Support line:** One command to install, works in Claude Code and Claude.ai,
  and its default (Tier 1) reducer never alters a single number, quote, or line
  of code.

## Feature value-props

- **Private and offline by default.** Tier 1 runs entirely on your machine using
  the Python standard library — no API key, no network, nothing leaves your box.
  The only path that hands text to a model is the opt-in semantic tier, which is
  clearly flagged as *lossy* and never runs unless you ask for it.
- **Safety-first by design.** It only removes *structural* redundancy — duplicate
  paragraphs, filler phrases, messy whitespace. Never rewrites numbers, quotes,
  code, names, or legal wording, and keeps the original when unsure.
- **One-command install.** A single `curl … | bash` drops the skill into place.
  No build step, no dependency tree, no config to babysit.
- **Works on both surfaces.** Skill + subagents inside Claude Code, or upload the
  ZIP into Claude.ai chat. Same engine, wherever you work.
- **Transparent and measurable.** Run with `--stats` to see real before/after
  token counts and the exact percentage saved on *your* text.
- **Workflow routing for big tasks.** For large refactors and multi-source
  research, `classify_task.py` splits the job map-reduce style: cheap Haiku
  subagents gather and condense, and only the distilled result reaches Sonnet/Opus.

## How it works

1. **Install once.** Run the one-line installer (or upload the ZIP to Claude.ai).
   Ready immediately — no keys, no setup.
2. **Reduce before you send.** Tier 1 normalizes whitespace, drops exact and
   near-duplicate paragraphs via difflib similarity, and trims filler — offline,
   without touching protected content.
3. **Measure and route.** Check actual savings with `--stats`. On large tasks in
   Claude Code, cheap subagents condense first so your capable model only reads
   what matters.

## Honest FAQ

**How much will this actually save me?**
It depends on your input, so measure it with `--stats` rather than trusting a
headline number. On genuinely unique, already-tight prose, expect almost nothing
— **~0–1%**, because a lossless pass can't shrink text that has no redundancy. On
repetitive or duplicated content, Tier 1 savings climb into the tens of percent.
The semantic tier can compress unique prose much further, but it's **lossy and
spends tokens to run**: it only *nets* a saving when a cheaper model condenses for
a pricier one, or when the digest is reused — and it's net-negative for one-shot,
same-model use. The biggest structural lever is workflow routing on large tasks,
where cheap subagents absorb the bulk before your expensive model sees it. We
won't quote a fixed percentage, because any tool that does is guessing about your
data.

**Is my data safe? Does it phone home?**
Tier 1 is 100% offline with zero dependencies — no API key, no network, nothing
leaves your machine. The only time text touches the Claude API is Tier 2
summarization, which is opt-in and only runs if you set a key yourself.

**Will it change my code or numbers?**
The default reducer never touches code, numbers, quotes, or legal wording — it
only removes structural redundancy, and keeps the original when unsure. There's
also an explicit opt-in **code mode** (`--code`) that strips comments and blank
lines from a *context copy* you feed the model — it preserves strings, numbers,
logic, shebangs, and directive comments (`eslint-disable`, `noqa`, `type:`, …),
and never rewrites your actual files. Code savings depend on comment density:
~8–12% on lightly-commented code, ~30–45% with docstring stripping, more in
signature-only "skeleton" mode. The opt-in **semantic tier** is different — it
*does* rewrite prose (that's the point), so it is lossy by nature; a deterministic
fidelity check runs afterward and fails **closed**, discarding the rewrite and
keeping your original if any number, code span, quote, or name didn't survive
verbatim.

**Claude Code vs Claude.ai — what's the difference?**
In Claude Code it installs as a skill and can use subagents for map-reduce
routing on large tasks. In Claude.ai you upload the ZIP and get the same Tier 1
engine. Both share the same safe, offline core. (Per-step model routing is a
Claude Code feature only.)

**Is it free?**
The software is free for noncommercial use — open source under the PolyForm
Noncommercial license. **Tier 1 costs nothing to run** — it's offline and spends
no tokens. The **semantic tier is not free**: it spends model (Haiku) tokens to
condense, so it only makes economic sense when a cheap model condenses for an
expensive one, or when you reuse the digest across turns. Tier 1 is, and will
stay, the zero-cost default.

## Launch blurbs

**X / Twitter (<280 chars):**

> Humanity built powerful AI. Now we optimize its fuel.
>
> ISAS Token Reducer: a Claude Skill that strips redundant text *before* Claude
> reads it. 100% offline by default, never touches your numbers/quotes/code,
> one-line install. Measure your own savings with --stats.
> github.com/IsasLab/isas-token-reducer

**LinkedIn / Reddit:**

> Most token waste happens before the model even starts thinking — duplicated
> paragraphs, filler phrases, and messy whitespace you're paying full price to
> process.
>
> ISAS Token Reducer is an open-source Claude Skill that cleans that up first:
>
> - **Offline by default** — Tier 1 runs on the Python standard library. No API
>   key, no network, nothing leaves your machine.
> - **Safe by design** — only removes structural redundancy. Never alters
>   numbers, quotes, code, names, or legal wording; keeps the original when unsure.
> - **Measurable** — `--stats` shows real before/after token counts. I won't
>   quote a magic number, because honest savings depend on how redundant your
>   input is: modest on clean text, higher on repetitive content, biggest when
>   workflow routing lets cheap subagents condense large tasks first.
> - **Both surfaces** — skill + subagents in Claude Code, ZIP upload in Claude.ai.
>
> One command to install. Free for noncommercial use (PolyForm Noncommercial).
>
> Don't optimize the engine. Refine the fuel.
> github.com/IsasLab/isas-token-reducer
