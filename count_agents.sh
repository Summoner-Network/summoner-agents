#!/usr/bin/env bash
set -euo pipefail

# --- config ---
README="README.md"
AGENTS_DIR="agents"
PATTERN="agent_*Agent*"

# --- sanity checks ---
if [[ ! -f "$README" ]]; then
  echo "❌ README.md not found at repo root"
  exit 1
fi

if [[ ! -d "$AGENTS_DIR" ]]; then
  echo "❌ agents/ directory not found"
  exit 1
fi

# --- count agents ---
AGENT_COUNT=$(find "$AGENTS_DIR" -maxdepth 1 -type d -name "$PATTERN" | wc -l | tr -d ' ')

echo "🔍 Found $AGENT_COUNT agents"

# --- update README ---
# Replace only the numeric value in the target sentence
sed -i.bak -E \
  "s/(There are )[0-9]+( agents available in this repo\.)/\1${AGENT_COUNT}\2/" \
  "$README"

rm -f "${README}.bak"

echo "✅ README.md updated successfully"
