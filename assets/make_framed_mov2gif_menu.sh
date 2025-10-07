#!/usr/bin/env bash
set -euo pipefail

# ---------- config ----------
FPS=25
WIDTH=800                 # final width INCLUDING border
BORDER=6                 # border width in pixels
BORDER_COLOR="7393B3"       # e.g., "gray", "#888888", "gray@1"
DITHER="bayer"            # good size/quality tradeoff
BAYER_SCALE=5             # tweak 3–7 to trade size vs banding
USE_GIFSICLE=0            # set to 1 if you want a final size pass (needs gifsicle)

# ---------- resolve dirs regardless of CWD ----------
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
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

  # Compute inner content width so that final width (including border) == WIDTH
  if (( 2*BORDER >= WIDTH )); then
    echo "Error: BORDER (${BORDER}) is too large for WIDTH (${WIDTH})." >&2
    return 1
  fi
  local INNER_WIDTH=$(( WIDTH - 2*BORDER ))
  if (( INNER_WIDTH < 16 )); then
    echo "Error: INNER_WIDTH ${INNER_WIDTH}px too small; decrease BORDER or increase WIDTH." >&2
    return 1
  fi

  local basename name palette output
  basename="$(basename "$input")"
  name="${basename%.*}"
  palette="${GIF_DIR}/${name}_palette.png"
  output="${GIF_DIR}/${name}_framed.gif"

  # Filters: scale to inner width, then pad with BORDER on each side
  local SCALE_FILTER="scale=${INNER_WIDTH}:-1:flags=lanczos"
  local PAD_FILTER="pad=${WIDTH}:ih+2*${BORDER}:${BORDER}:${BORDER}:color=${BORDER_COLOR}"

  echo "Generating palette → $(basename "$palette")"
  ffmpeg -y -i "$input" \
    -vf "fps=${FPS},${SCALE_FILTER},${PAD_FILTER},palettegen=stats_mode=full" \
    "$palette"

  echo "Encoding GIF → $(basename "$output")"
  ffmpeg -y -i "$input" -i "$palette" \
    -filter_complex "[0:v]fps=${FPS},${SCALE_FILTER},${PAD_FILTER}[v];[v][1:v]paletteuse=dither=${DITHER}:bayer_scale=${BAYER_SCALE}:diff_mode=rectangle" \
    -gifflags -offsetting \
    "$output"

  if (( USE_GIFSICLE == 1 )); then
    if command -v gifsicle >/dev/null 2>&1; then
      # Lossless optimize first; uncomment --lossy=N (e.g., 20–40) if you want smaller files with some quality tradeoff.
      # gifsicle --lossy=25 -O3 "$output" -o "$output"
      gifsicle -O3 "$output" -o "$output"
    else
      echo "Note: gifsicle not found; skipping final optimization."
    fi
  fi

  echo "Done → $output"
}

# ---------- build MOV list (Bash 3.2 safe) ----------
MOVS=()
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
