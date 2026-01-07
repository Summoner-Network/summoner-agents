#!/usr/bin/env bash
set -euo pipefail

# --- config ---
README="README.md"
AGENTS_DIR="agents"
PATTERN="agent_*Agent*"

# Agent directory globs that should NOT be counted
EXCLUDED_PATTERNS=(
  "$AGENTS_DIR/agent_*_x"
  "$AGENTS_DIR/agent_*.old"
)

# --- sanity checks ---
if [[ ! -f "$README" ]]; then
  echo "❌ README.md not found at repo root"
  exit 1
fi

if [[ ! -d "$AGENTS_DIR" ]]; then
  echo "❌ agents/ directory not found"
  exit 1
fi

# --- build find exclusions ---
FIND_EXCLUDES=()
for pattern in "${EXCLUDED_PATTERNS[@]}"; do
  FIND_EXCLUDES+=( ! -path "$pattern" )
done

# --- count agents ---
AGENT_COUNT=$(
  find "$AGENTS_DIR" -maxdepth 1 -type d -name "$PATTERN" \
    "${FIND_EXCLUDES[@]}" \
  | wc -l | tr -d ' '
)

echo "🔍 Found $AGENT_COUNT agents"

# --- update README ---
sed -i.bak -E \
  "s/(There are )[0-9]+( agents available in this repo\.)/\1${AGENT_COUNT}\2/" \
  "$README"

rm -f "${README}.bak"

echo "✅ README.md updated successfully"
