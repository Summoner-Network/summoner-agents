#!/usr/bin/env bash
set -euo pipefail

# --- config ---
README="README.md"
AGENTS_DIR="agents"
PATTERN="agent_*Agent*"

# Agents that should NOT be counted
EXCLUDED_AGENTS=(
  "agent_InputPresentAgent"
  "agent_InputCmdAgent"
  "agent_GPTPresentAgent"
  "agent_CatUpdateAgent_0.old"
  "agent_CatUpdateAgent_1.old"
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

# --- build exclusion regex ---
EXCLUDE_REGEX="$(printf '(%s)|' "${EXCLUDED_AGENTS[@]}")"
EXCLUDE_REGEX="${EXCLUDE_REGEX%|}"

# --- count agents ---
AGENT_COUNT=$(
  find "$AGENTS_DIR" -maxdepth 1 -type d -name "$PATTERN" \
  | grep -v -E "$EXCLUDE_REGEX" \
  | wc -l | tr -d ' '
)

echo "🔍 Found $AGENT_COUNT agents"

# --- update README ---
sed -i.bak -E \
  "s/(There are )[0-9]+( agents available in this repo\.)/\1${AGENT_COUNT}\2/" \
  "$README"

rm -f "${README}.bak"

echo "✅ README.md updated successfully"
