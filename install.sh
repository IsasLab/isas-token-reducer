#!/usr/bin/env bash
#
# ISAS Token Reducer — one-command installer for Claude Code.
#
#   curl -fsSL https://raw.githubusercontent.com/IsasLab/isas-token-reducer/main/install.sh | bash
#
# Installs the skill into ~/.claude/skills/isas-token-reducer and copies the
# workflow-routing subagents into ~/.claude/agents/. No sudo, no extra
# dependencies (Python standard library is enough; tiktoken is optional).
#
# Testing / advanced overrides (env vars):
#   CLAUDE_DIR   base dir instead of ~/.claude (point at a temp dir to test)
#   ISAS_SOURCE  install from a local repo path instead of cloning from GitHub
#   ISAS_DRY_RUN =1 prints what would happen and makes no changes
#
set -euo pipefail

REPO_URL="https://github.com/IsasLab/isas-token-reducer"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
SKILLS_DIR="$CLAUDE_DIR/skills"
AGENTS_DIR="$CLAUDE_DIR/agents"
SKILL_DIR="$SKILLS_DIR/isas-token-reducer"
DRY="${ISAS_DRY_RUN:-0}"

say()  { printf '%s\n' "$*"; }
err()  { printf 'ERROR: %s\n' "$*" >&2; }
run()  { if [ "$DRY" = "1" ]; then say "  [dry-run] $*"; else eval "$*"; fi; }

say "ISAS Token Reducer — installer"
[ "$DRY" = "1" ] && say "(dry run — no changes will be made)"

# 1. Is Claude Code installed?  ~/.claude must exist.
if [ ! -d "$CLAUDE_DIR" ]; then
  err "Claude Code config dir not found at: $CLAUDE_DIR"
  err "Claude Code doesn't appear to be installed for this user."
  err "Install it first: https://docs.claude.com/en/docs/claude-code/overview"
  exit 1
fi

run "mkdir -p \"$SKILLS_DIR\" \"$AGENTS_DIR\""

# 2. Install / update the skill.
if [ -n "${ISAS_SOURCE:-}" ]; then
  # Local install (used for testing and for offline/proxy environments).
  say "Installing skill from local source: $ISAS_SOURCE"
  run "rm -rf \"$SKILL_DIR\""
  run "mkdir -p \"$SKILL_DIR\""
  run "cp -R \"$ISAS_SOURCE\"/. \"$SKILL_DIR\"/"
  # Drop build artifacts / local tooling that shouldn't ship in the skill dir.
  run "rm -rf \"$SKILL_DIR/.git\" \"$SKILL_DIR/.code-review-graph\" \"$SKILL_DIR/isas-token-reducer.zip\""
  run "find \"$SKILL_DIR\" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true"
elif [ -d "$SKILL_DIR/.git" ]; then
  say "Skill already present — updating (git pull)…"
  run "git -C \"$SKILL_DIR\" pull --ff-only"
elif [ -d "$SKILL_DIR" ]; then
  say "Skill dir exists but is not a git clone — refreshing via clone…"
  run "rm -rf \"$SKILL_DIR\""
  run "git clone --depth 1 \"$REPO_URL\" \"$SKILL_DIR\""
else
  say "Cloning skill into $SKILL_DIR …"
  run "git clone --depth 1 \"$REPO_URL\" \"$SKILL_DIR\""
fi

# 3. Copy workflow-routing subagents into ~/.claude/agents/.
if [ "$DRY" = "1" ]; then
  say "  [dry-run] cp \"$SKILL_DIR\"/agents/*.md \"$AGENTS_DIR\"/"
elif ls "$SKILL_DIR"/agents/*.md >/dev/null 2>&1; then
  cp "$SKILL_DIR"/agents/*.md "$AGENTS_DIR"/
  say "Copied workflow-routing subagents into $AGENTS_DIR"
else
  err "No agents/*.md found in the skill — subagents not installed."
fi

# 4. Success.
say ""
say "Done. Installed:"
say "  - skill:    $SKILL_DIR"
say "  - agents:   context-scout, research-gatherer, implementer, synthesizer -> $AGENTS_DIR"
say ""
say "No restart needed — Claude Code discovers skills and agents on the next turn."
say ""
say "Try it now, e.g.:"
say "  \"Reduce the tokens in the long text I'm about to paste before you process it.\""
