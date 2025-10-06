#!/usr/bin/env bash
set -euo pipefail

# ---------- config ----------
FPS=25
WIDTH=800
SCALE_FILTER="scale=${WIDTH}:-1:flags=lanczos"

# ---------- resolve dirs regardless of CWD ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

# If the script is inside assets/mov2gif/, MOV2GIF_DIR is SCRIPT_DIR.
# If the script is inside assets/, MOV2GIF_DIR is "$SCRIPT_DIR/mov2gif".
if [[ -d "$SCRIPT_DIR/movs" && -d "$SCRIPT_DIR/gifs" ]]; then
  MOV2GIF_DIR="$SCRIPT_DIR"
else
  MOV2GIF_DIR="$SCRIPT_DIR/mov2gif"
fi

MOVS_DIR="$MOV2GIF_DIR/movs"
GIF_DIR="$MOV2GIF_DIR/gifs"

# ---------- checks ----------
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Error: ffmpeg not found. Install it (e.g., 'brew install ffmpeg' on macOS)." >&2
  exit 1
fi
if [[ ! -d "$MOVS_DIR" ]]; then
  echo "Error: movs directory not found at: $MOVS_DIR" >&2
  exit 1
fi
mkdir -p "$GIF_DIR"

# ---------- convert one file ----------
make_one() {
  input="$1"

  case "$input" in
    *.mov) ;;
    *) echo "Skipping non-.mov: $input" >&2; return 0 ;;
  esac
  if [[ ! -f "$input" ]]; then
    echo "Not found: $input" >&2
    return 1
  fi

  basename="$(basename "$input")"
  name="${basename%.*}"
  palette="${GIF_DIR}/${name}_palette.png"
  output="${GIF_DIR}/${name}.gif"

  echo "Generating palette → $(basename "$palette")"
  ffmpeg -y -i "$input" -vf "fps=${FPS},${SCALE_FILTER},palettegen" "$palette"

  echo "Encoding GIF → $(basename "$output")"
  ffmpeg -y -i "$input" -i "$palette" \
    -filter_complex "fps=${FPS},${SCALE_FILTER}[x];[x][1:v]paletteuse" \
    "$output"

  echo "Done → $output"
}

# ---------- build MOV list (Bash 3.2 safe) ----------
MOVS=()
# Use -print0 to handle spaces; read -d '' is supported in Bash 3.2
while IFS= read -r -d '' f; do
  MOVS+=("$f")
done < <(find "$MOVS_DIR" -maxdepth 1 -type f -name '*.mov' -print0 | sort -z)

if (( ${#MOVS[@]} == 0 )); then
  echo "No .mov files found in: $MOVS_DIR"
  exit 1
fi

# ---------- interactive menu ----------
while true; do
  echo
  echo "Available .mov files in: $MOVS_DIR"
  idx=1
  for f in "${MOVS[@]}"; do
    printf "%2d) %s\n" "$idx" "$(basename "$f")"
    idx=$((idx+1))
  done
  echo " q) quit"
  echo

  printf "Select a movie by index (1-%d) or 'q' to quit: " "${#MOVS[@]}"
  IFS= read -r choice

  case "$choice" in
    q|Q) echo "Bye."; exit 0 ;;
    *)
      # numeric?
      if [[ "$choice" =~ ^[0-9]+$ ]]; then
        sel=$((choice))
        if (( sel >= 1 && sel <= ${#MOVS[@]} )); then
          make_one "${MOVS[$((sel-1))]}"
        else
          echo "Out of range."
        fi
      else
        echo "Invalid input."
      fi
      ;;
  esac
done
