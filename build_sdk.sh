#!/usr/bin/env bash

set -e  # only -e, no -u so sourcing doesn’t abort on unset vars

# ─────────────────────────────────────────────────────
# Detect if script is being sourced or executed
# ─────────────────────────────────────────────────────
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  SCRIPT_SOURCED=1
else
  SCRIPT_SOURCED=0
fi

die() {
  echo "❌ $*" >&2
  if [[ $SCRIPT_SOURCED -eq 1 ]]; then
    return 1
  else
    exit 1
  fi
}

usage() {
  die "Usage: $0 {setup|delete|reset|deps|test_server|clean} [build|test_build]"
}

# ─────────────────────────────────────────────────────
# Paths & Config
# ─────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_REPO="https://github.com/Summoner-Network/summoner-core.git"
CORE_BRANCH="main"
SRC="$ROOT/summoner-sdk"
BUILD_FILE_BUILD="$ROOT/build.txt"
BUILD_FILE_TEST="$ROOT/test_build.txt"
BUILD_LIST="$BUILD_FILE_BUILD"
VENVDIR="$ROOT/venv"
PYTHON="python3"

# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────

# GNU sed:        sed --version succeeds → use “-i”
# BSD/macOS sed:  sed --version fails     → use “-i ''”
if sed --version >/dev/null 2>&1; then
  SED_INPLACE=(-i)
else
  SED_INPLACE=(-i '')
fi

## Add these definitions at the top of your script, after detecting shell and before functions
RED=$'\033[31m'   # red
GREEN=$'\033[32m' # green
RESET=$'\033[0m'  # reset

## Updated rewrite_imports() with colored before/after lines
rewrite_imports() {
  local _unused_pkg=$1 dir=$2
  echo "    🔎 Rewriting imports in $dir"

  find "$dir" -type f -name '*.py' -print0 \
  | while IFS= read -r -d '' file; do
      echo "    📄 Processing: $file"

      # Before: red (only show tooling.* lines)
      echo "      ↪ Before:"
      if grep -E '^[[:space:]]*#?[[:space:]]*from[[:space:]]+tooling\.' "$file" >/dev/null; then
        grep -E '^[[:space:]]*#?[[:space:]]*from[[:space:]]+tooling\.' "$file" \
          | sed -e "s/^/        ${RED}/" -e "s/$/${RESET}/"
      else
        echo "        (no matches)"
      fi

      # snapshot
      tmp_before=$(mktemp) || { echo "      ❌ mktemp failed"; continue; }
      cp "$file" "$tmp_before"

      # in-place replacements: tooling.* → summoner.*  (do NOT touch summoner.*)
      sed -E "${SED_INPLACE[@]}" \
        -e 's/^([[:space:]]*#?[[:space:]]*)from[[:space:]]+tooling\.([[:alnum:]_]+)/\1from summoner.\2/' \
        "$file"

      # After: green
      echo "      ↪ After:"
      after_lines=$(diff -u "$tmp_before" "$file" \
        | awk 'NR>=4 && /^\+[^+]/ { print substr($0,2) }')
      if [ -n "$after_lines" ]; then
        printf '%s\n' "$after_lines" \
          | sed -e "s/^/        ${GREEN}/" -e "s/$/${RESET}/"
      else
        echo "        (no visible changes)"
      fi

      rm -f "$tmp_before"
    done
}


clone_native() {
  local url=$1 name
  name=$(basename "$url" .git)
  echo "📥 Cloning native repo: $name"
  git clone --depth 1 "$url" native_build/"$name"
}

# ─────────────────────────────────────────────────────
# Merge one native repo’s tooling/
# ─────────────────────────────────────────────────────
merge_tooling() {
  repo_url=$1; shift
  features="$*"
  # extract “name” from URL
  name=${repo_url##*/}
  name=${name%.git}
  srcdir="native_build/$name/tooling"
  if [ ! -d "$srcdir" ]; then
    echo "⚠️  No tooling/ in $name, skipping"
    return
  fi

  echo "  🔀 Processing tooling in $name"
  if [ -z "$features" ]; then
    # copy everything
    for pkg_dir in "$srcdir"/*; do
      [ -d "$pkg_dir" ] || continue
      pkg=${pkg_dir##*/}
      dest="$SRC/summoner/$pkg"
      echo "    🚚 Adding package: $pkg"
      cp -R "$pkg_dir" "$dest"
      rewrite_imports "$pkg" "$dest"
    done
  else
    # only copy listed features
    for pkg in $features; do
      if [ -d "$srcdir/$pkg" ]; then
        dest="$SRC/summoner/$pkg"
        echo "    🚚 Adding package: $pkg"
        cp -R "$srcdir/$pkg" "$dest"
        rewrite_imports "$pkg" "$dest"
      else
        echo "    ⚠️  $name/tooling/$pkg not found, skipping"
      fi
    done
  fi
}

# ─────────────────────────────────────────────────────
# Core Workflows
# ─────────────────────────────────────────────────────
bootstrap() {
  echo "🔧 Bootstrapping environment…"

  # 1) Clone core
  if [ ! -d "$SRC" ]; then
    echo "  📥 Cloning Summoner core → $SRC"
    git clone --depth 1 --branch "$CORE_BRANCH" "$CORE_REPO" "$SRC"
  fi

  # 2) Validate build list
  echo "  🔄 Using build list: $BUILD_LIST"
  [ -f "$BUILD_LIST" ] || die "Missing build list: $BUILD_LIST"

  # show sanitized list
  echo
  echo "  🔄 Sanitized build list:"
  sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$BUILD_LIST" \
    | sed 's/^/    /'
  echo

  # ─────────────────────────────────────────────────────
  # 3+4) POSIX-sh parse BUILD_LIST, clone & merge tooling
  # ─────────────────────────────────────────────────────
  echo "  📋 Parsing $BUILD_LIST and merging tooling…"
  rm -rf native_build || true
  mkdir -p native_build
  mkdir -p "$SRC/summoner"

  current_url=
  current_features=

  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    # strip DOS CR
    # line=${raw_line%$'\r'}
    line=${raw_line%
    }
    # trim leading/trailing whitespace
    set -- $line
    line=$*

    # skip empty or comment
    case "$line" in
      ''|\#*) continue ;;
    esac

    case "$line" in
      *.git:)
        # a URL with trailing ':' → finish previous block
        if [ -n "$current_url" ]; then
          clone_native "$current_url"
          merge_tooling "$current_url" $current_features
        fi
        current_url=${line%:}
        current_features=
        ;;
      *.git)
        # a bare URL → also finish previous, start new
        if [ -n "$current_url" ]; then
          clone_native "$current_url"
          merge_tooling "$current_url" $current_features
        fi
        current_url=$line
        current_features=
        ;;
      *)
        # a feature name → accumulate
        if [ -z "$current_features" ]; then
          current_features=$line
        else
          current_features="$current_features $line"
        fi
        ;;
    esac
  done < "$BUILD_LIST"

  # final repo
  if [ -n "$current_url" ]; then
    clone_native "$current_url"
    merge_tooling "$current_url" $current_features
  fi

  # ─────────────────────────────────────────────────────
  # 5) Create & activate venv … etc.
  # ─────────────────────────────────────────────────────
  if [ ! -d "$VENVDIR" ]; then
    echo "  🐍 Creating virtualenv → $VENVDIR"
    $PYTHON -m venv "$VENVDIR"
  fi
  # shellcheck source=/dev/null
  source "$VENVDIR/bin/activate"

  # ─────────────────────────────────────────────────────
  # Install native‐repo requirements if present
  # ─────────────────────────────────────────────────────
  echo "  📦 Checking for native-repo requirements…"
  for repo_dir in native_build/*; do
    name=$(basename "$repo_dir")
    req="$repo_dir/requirements.txt"
    if [ -f "$req" ]; then
      echo "    ▶ Installing requirements for $name"
      $PYTHON -m pip install -r "$req"
    else
      echo "    ⚠️  $name has no requirements.txt, skipping"
    fi
  done

  # 6) Install build tools
  echo "  📦 Installing build requirements"
  pip install --upgrade pip setuptools wheel maturin

  # 7) Write .env
  echo "  📝 Writing .env"
  cat > "$SRC/.env" <<EOF
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
EOF

  # 8) Reinstall extras
  echo "  🔁 Running reinstall_python_sdk.sh"
  bash "$SRC/reinstall_python_sdk.sh" rust_server_v1_0_0

  echo "✅ Setup complete! You are now in the venv."
}

delete() {
  echo "🔄 Deleting environment…"
  rm -rf "$SRC" "$VENVDIR" native_build "$ROOT"/logs || true
  rm -f test_*.{py,json} || true
  echo "✅ Deletion complete"
}

reset() {
  echo "🔄 Resetting environment…"
  delete
  bootstrap
  echo "✅ Reset complete!"
}

deps() {
  echo "🔧 Reinstalling dependencies…"
  [ -d "$VENVDIR" ] || die "Run setup first"
  source "$VENVDIR/bin/activate"
  bash "$SRC/reinstall_python_sdk.sh" rust_server_v1_0_0
  echo "✅ Dependencies reinstalled!"
}

test_server() {
  echo "🔧 Running test_server…"
  [ -d "$VENVDIR" ] || die "Run setup first"
  source "$VENVDIR/bin/activate"
  cp "$SRC/desktop_data/default_config.json" test_server_config.json
  cat > test_server.py <<'EOF'
from summoner.server import SummonerServer
from summoner.your_package import hello_summoner

if __name__ == "__main__":
    hello_summoner()
    SummonerServer(name="test_Server").run(config_path="test_server_config.json")
EOF
  python test_server.py
}

clean() {
  echo "🧹 Cleaning generated files…"
  rm -rf native_build "$ROOT"/logs/* || true
  rm -f test_*.{py,json} || true
  echo "✅ Clean complete"
}

# ─────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────
case "${1:-}" in
  setup)
    variant="${2:-build}"
    case "$variant" in
      build)      BUILD_LIST="$BUILD_FILE_BUILD" ;;
      test_build) BUILD_LIST="$BUILD_FILE_TEST"  ;;
      *)          die "Unknown setup variant: $variant (use 'build' or 'test_build')" ;;
    esac
    bootstrap
    ;;
  delete)       delete       ;;
  reset)        reset        ;;
  deps)         deps         ;;
  test_server)  test_server  ;;
  clean)        clean       ;;
  *)            usage       ;;
esac
