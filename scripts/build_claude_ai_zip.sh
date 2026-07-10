#!/usr/bin/env bash
#
# Build the Claude.ai skill upload archive.
#
# Produces ./isas-token-reducer.zip in the repo root, with the skill files under
# an "isas-token-reducer/" root directory INSIDE the archive — this structure is
# what Claude.ai expects (SKILL.md at isas-token-reducer/SKILL.md). Getting this
# wrong is the #1 reason an uploaded skill never triggers.
#
# Uses the `zip` CLI when available, otherwise falls back to Python's stdlib
# zipfile (so it works on machines without `zip`, e.g. stock Windows/Git Bash).
#
set -euo pipefail

# Repo root = parent of this script's dir.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ARCHIVE_NAME="isas-token-reducer.zip"
ARCHIVE_PATH="$ROOT/$ARCHIVE_NAME"
PREFIX="isas-token-reducer"

# Files/dirs to include in the upload (relative to repo root).
INCLUDE=(
  "SKILL.md"
  "README.md"
  "LICENSE"
  "scripts"
  "references"
  "examples"
  "agents"
)

cd "$ROOT"

# Verify required top-level file exists.
if [ ! -f "SKILL.md" ]; then
  echo "ERROR: SKILL.md not found in $ROOT — run this from within the repo." >&2
  exit 1
fi

rm -f "$ARCHIVE_PATH"

# Only include paths that actually exist.
EXISTING=()
for item in "${INCLUDE[@]}"; do
  [ -e "$item" ] && EXISTING+=("$item")
done

if command -v zip >/dev/null 2>&1; then
  echo "Building $ARCHIVE_NAME with the zip CLI…"
  # Stage under the prefix dir so the archive root is isas-token-reducer/.
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/$PREFIX"
  for item in "${EXISTING[@]}"; do
    cp -R "$item" "$TMP/$PREFIX/"
  done
  find "$TMP" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  ( cd "$TMP" && zip -r -q "$ARCHIVE_PATH" "$PREFIX" -x '*.pyc' )
else
  echo "zip CLI not found — using Python stdlib fallback…"
  python - "$ARCHIVE_PATH" "$PREFIX" "${EXISTING[@]}" <<'PY'
import os, sys, zipfile
archive, prefix = sys.argv[1], sys.argv[2]
items = sys.argv[3:]
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for item in items:
        if os.path.isfile(item):
            z.write(item, f"{prefix}/{item}")
        else:
            for dirpath, dirs, files in os.walk(item):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if f.endswith(".pyc"):
                        continue
                    full = os.path.join(dirpath, f)
                    z.write(full, f"{prefix}/{full}".replace(os.sep, "/"))
print(f"wrote {archive}")
PY
fi

echo ""
echo "Built: $ARCHIVE_PATH"
echo "Verify structure (SKILL.md must appear as $PREFIX/SKILL.md):"
if command -v unzip >/dev/null 2>&1; then
  unzip -l "$ARCHIVE_PATH" | grep -E "$PREFIX/SKILL.md" || echo "  (warning: SKILL.md not found at expected path!)"
else
  python - "$ARCHIVE_PATH" "$PREFIX" <<'PY'
import sys, zipfile
archive, prefix = sys.argv[1], sys.argv[2]
names = zipfile.ZipFile(archive).namelist()
target = f"{prefix}/SKILL.md"
print("  OK:", target) if target in names else print("  WARNING: SKILL.md not at", target)
PY
fi
