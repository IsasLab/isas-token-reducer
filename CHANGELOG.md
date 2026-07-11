# Changelog

All notable changes to this project are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

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
