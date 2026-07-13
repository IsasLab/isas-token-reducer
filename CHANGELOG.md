# Changelog

All notable changes to this project are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-07-12

Honest hybrid rebuild. The free, offline, byte-safe deterministic pass stays the
default; an explicitly **lossy** semantic tier is added for the cases Tier 1 can't
help; and every published percentage is now conditional. No headline number —
`--stats` and `--auto` report figures measured on *your* input. The guiding
economics: a cheap model condensing for an expensive one (or a reused digest)
saves tokens; a one-shot same-model condense loses them, and the tool says so.

### Added
- **Semantic tier (opt-in, lossy).** Primary path is the skill-orchestrated
  `context-condenser` subagent (`model: haiku`, no API key, no dependency): it
  reads raw material in an isolated context and returns only a dense digest, so
  the expensive model never ingests the raw dump. Backed by `scripts/semantic.py`
  — an offline-testable core (chunking that never splits a code fence, prompt
  building, required-span extraction) plus a live path that lazy-imports the SDK
  and degrades gracefully when it or `ANTHROPIC_API_KEY` is absent.
- **Fail-closed fidelity guardrail.** `verify_fidelity()` reuses the Tier-1
  extractors to assert every number (order-aware), code span, quote, and proper
  noun in the source survives verbatim in the digest; on any miss the digest is
  discarded and the Tier-1/original text is used. Legal, contract, and verbatim
  quotation text are blocked from semantic condensing entirely.
- **`--auto` advisor.** Counts raw tokens, runs the free Tier-1 pass, scores
  redundancy, and prints one honest verdict per input — Tier 1 handled it /
  unique-and-small so send as-is / low-redundancy-but-large so the semantic tier
  may net-save cross-model or on reuse / **net-negative one-shot same-model,
  refusing by default**. It never runs the lossy tier itself and keeps output
  non-destructive.
- **New deterministic Tier-1 passes**, all still byte-safe and skipping
  fenced/inline code: sentence-level number- and negation-aware near-duplicate
  removal, list-item dedup, lossless GFM table compaction, zero-width/BOM
  invisible-character stripping, a completed `normalize_markdown` (heading/list
  tidy), and an opt-in `--strip-html-comments`.
- **Wider code-mode coverage:** `--` line comments (Lua, SQL), `#` for PowerShell
  `.ps1`, and `;`/`#` for `.ini`/`.cfg`/`.conf`, all keeping the
  parse-failure-returns-source-unchanged safety posture.
- **`--stats-json`** on `reduce.py`/`reduce_code.py`, plus a `reduce_with_report()`
  API returning per-stage token deltas and a redundancy score, so tooling can
  branch on numbers instead of parsing prose.

### Changed
- **Shared token counter (`count_tokens.py`).** The `cl100k_base` encoder is now
  cached at module level instead of being rebuilt on every call. Added an opt-in
  `count_tokens_exact()` that calls the Anthropic token-counting API for a true
  Claude count; the default rung and the load-bearing `(int, method)` return
  contract are unchanged, so every existing caller keeps working.
- **Documentation made conditional and honest.** README, MARKETING, and the
  landing page now state the ~0–1% information-theory floor on unique prose, label
  the high-savings tables as *deliberately redundant fixtures*, scope the "never
  alters a number/quote/code" promise explicitly to Tier 1, and warn that the
  semantic tier is lossy and net-negative for one-shot same-model use. The
  `cl100k` counter is relabelled an approximate *GPT* tokenizer for Claude, with
  the same-method percentage — not the absolute count — called out as the
  reliable figure.
- **`--tier2` demoted** in the docs from "the" semantic path to a secondary
  programmatic SDK fallback for pipelines outside Claude Code; the
  `context-condenser` subagent is now the primary semantic route.

## [1.1.0] - 2026-07-11

### Added
- **Reasoning-preservation benchmark** (`benchmarks/reasoning_preservation.py`):
  a paired GSM8K answer-stability harness that measures whether input reduction
  changes downstream accuracy, across every level and with tier2 on/off. Ships
  with exact-match extraction, Wilson CIs, an exact McNemar test, sample-size /
  power tables, and a numeric-fidelity check — all runnable offline via
  `--selftest` (no API key required). Dataset is pinned to a specific GSM8K
  commit and verified by sha256 for byte-reproducibility.
- Regression tests guarding the number-aware near-duplicate fix
  (`tests/test_safety.py`) and the benchmark self-test wired into CI.

### Fixed
- **Safety hole in near-duplicate removal.** `remove_near_duplicates` could
  collapse two paragraphs that read almost identically but carried a *different*
  number (e.g. "…12 apples…" vs "…18 apples…"), silently dropping a figure and
  contradicting the "numbers are never altered" guarantee. Near-duplicate
  removal is now number-aware: paragraphs are only merged when their set of
  numeric literals is identical. Genuine reworded near-duplicates (same numbers)
  still collapse; measured savings on the example fixtures are unchanged.

## [1.0.0] - 2026-07-11

### Added
- Tier 1: whitespace normalization, exact & near-duplicate paragraph removal,
  duplicate-sentence removal, filler-phrase trimming, verbose-phrase
  compression, lossless JSON minification, markdown normalization.
- Tier 2 (opt-in): Claude API summarization for long blocks.
- Code mode (`--code`): comment/blank-line stripping for a source-code
  context copy, preserving strings, numbers, logic, and directive comments.
- Workflow-routing subagents for Claude Code (context-scout,
  research-gatherer, implementer, synthesizer).
- One-command installer (`install.sh`) for Claude Code.
- Claude.ai ZIP build script (`scripts/build_claude_ai_zip.sh`).
- Safety test suite (`tests/test_safety.py`) verifying numbers, code,
  quotes, and JSON payloads are never altered across all reduction levels.
- CI via GitHub Actions across Python 3.9–3.12.
