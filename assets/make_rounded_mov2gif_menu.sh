#!/usr/bin/env bash
set -euo pipefail

# ---------- config via env ----------
WIDTH="${WIDTH:-800}"
RADIUS="${RADIUS:-25}"
BORDER="${BORDER:-2}"
BORDER_COLOR="${BORDER_COLOR:-128,128,128,255}"
MAX_COLORS="${MAX_COLORS:-128}"
DITHER="${DITHER:-bayer}"
BAYER_SCALE="${BAYER_SCALE:-5}"
LOOP="${LOOP:-0}"

# Budget & frame-search knobs (NEW)
TARGET_MB="${TARGET_MB:-5}"
FRAMES_INIT="${FRAMES_INIT:-1000}"
FRAMES_MIN="${FRAMES_MIN:-64}"
FRAMES_MAX="${FRAMES_MAX:-32768}"
MAX_FPS="${MAX_FPS:-25}"
VERBOSE="${VERBOSE:-0}"
STRICT="${STRICT:-0}"

# ---------- locate dirs ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
if [[ -d "$SCRIPT_DIR/mov2gif/movs" && -d "$SCRIPT_DIR/mov2gif/gifs" ]]; then
  MOV2GIF_DIR="$SCRIPT_DIR/mov2gif"
elif [[ -d "$SCRIPT_DIR/movs" && -d "$SCRIPT_DIR/gifs" ]]; then
  MOV2GIF_DIR="$SCRIPT_DIR"
else
  echo "Error: cannot locate movs/gifs under $SCRIPT_DIR" >&2; exit 1
fi

MOVS_DIR="$MOV2GIF_DIR/movs"
GIF_DIR="$MOV2GIF_DIR/gifs"
PY_ROUNDIFY="$SCRIPT_DIR/roundify.py"

command -v ffmpeg >/dev/null 2>&1 || { echo "Error: ffmpeg not found." >&2; exit 1; }
command -v ffprobe >/dev/null 2>&1 || { echo "Error: ffprobe not found." >&2; exit 1; }
[[ -f "$PY_ROUNDIFY" ]] || { echo "Error: roundify.py not found at $PY_ROUNDIFY" >&2; exit 1; }
mkdir -p "$GIF_DIR"

# ---------- gather movs ----------
MOVS=()
while IFS= read -r -d '' f; do MOVS+=("$f"); done < <(find "$MOVS_DIR" -maxdepth 1 -type f -name '*.mov' -print0 | sort -z)
(( ${#MOVS[@]} )) || { echo "No .mov files in $MOVS_DIR"; exit 0; }

run_one () {
  python3 "$PY_ROUNDIFY" \
    --out-dir "$GIF_DIR" \
    --width "$WIDTH" \
    --radius "$RADIUS" \
    --border "$BORDER" \
    --border-color "$BORDER_COLOR" \
    --max-colors "$MAX_COLORS" \
    --dither "$DITHER" \
    --bayer-scale "$BAYER_SCALE" \
    --loop "$LOOP" \
    --target-mb "$TARGET_MB" \
    --frames-init "$FRAMES_INIT" \
    --frames-min "$FRAMES_MIN" \
    --frames-max "$FRAMES_MAX" \
    --max-fps "$MAX_FPS" \
    $([ "$VERBOSE" = "1" ] && echo "--verbose") \
    $([ "$STRICT" = "1" ] && echo "--strict") \
    "$@"
}

# ---------- menu ----------
while true; do
  echo
  echo "Available .mov files in: $MOVS_DIR"
  idx=1
  for f in "${MOVS[@]}"; do printf "%2d) %s\n" "$idx" "$(basename "$f")"; idx=$((idx+1)); done
  echo " a) all"
  echo " q) quit"
  echo
  printf "Select index (1-%d), 'a' for all, or 'q' to quit: " "${#MOVS[@]}"
  IFS= read -r choice

  case "$choice" in
    q|Q) echo "Bye."; exit 0 ;;
    a|A) run_one "${MOVS[@]}" ;;
    *)
      if [[ "$choice" =~ ^[0-9]+$ ]]; then
        sel=$((choice))
        if (( sel >= 1 && sel <= ${#MOVS[@]} )); then
          run_one "${MOVS[$((sel-1))]}"
        else
          echo "Out of range."
        fi
      else
        echo "Invalid input."
      fi
      ;;
  esac
done
