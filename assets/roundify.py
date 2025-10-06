#!/usr/bin/env python3
import argparse, os, sys, shutil, subprocess, tempfile, math, time
from PIL import Image, ImageDraw

def fail(msg): print(f"Error: {msg}", file=sys.stderr); sys.exit(1)

def ffprobe_size(path):
    out = subprocess.check_output([
        "ffprobe","-v","error","-select_streams","v:0",
        "-show_entries","stream=width,height","-of","csv=s=,:p=0", path
    ], text=True).strip()
    w, h = out.split(","); return int(w), int(h)

def ffprobe_duration(path):
    out = subprocess.check_output([
        "ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1", path
    ], text=True).strip()
    try: return float(out)
    except: return None

def scaled_size(src_w, src_h, target_w=None):
    if target_w is None or target_w >= src_w: return src_w, src_h
    s = target_w / float(src_w)
    return max(1, int(round(src_w*s))), max(1, int(round(src_h*s)))

def build_border_only(w, h, radius, border_px, border_rgba):
    W, H = w, h
    img = Image.new("RGBA", (W, H), (0,0,0,0))
    ring = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(ring)
    d.rounded_rectangle((0,0,W-1,H-1), radius=radius, fill=255)
    inset = border_px
    if W > 2*inset and H > 2*inset:
        d.rounded_rectangle((inset,inset,W-1-inset,H-1-inset), radius=max(0, radius-border_px), fill=0)
    ring_rgba = Image.new("RGBA", (W, H), border_rgba)
    ring_rgba.putalpha(ring)
    img.alpha_composite(ring_rgba)
    return img

def encode_once(input_mov, frame_png, out_gif,
                width_out, height_out, fps, max_colors, dither, bayer_scale, loop,
                verbose=False, tag=""):
    """Two-pass encode with border-only overlay. Returns (size_bytes, elapsed_s)."""
    t0 = time.time()

    base = f"fps={fps:.6f},setsar=1,scale={width_out}:{height_out}:flags=lanczos,format=rgb24"
    bor  = "format=rgba"
    palgen = f"palettegen=stats_mode=diff:max_colors={max_colors}"
    paluse = f"paletteuse=dither={dither}:bayer_scale={bayer_scale}:diff_mode=rectangle"

    common = ["-hide_banner","-nostdin","-threads","2","-filter_threads","1"]
    if verbose: common += ["-v","warning","-stats","-stats_period","0.5"]
    else:       common += ["-v","error"]

    with tempfile.TemporaryDirectory() as td:
        palette_path = os.path.join(td, "palette.png")

        print(f"[roundify]  {tag} PASS 1/2 palettegen (fps={fps:.3f}, {width_out}x{height_out}, colors={max_colors})")
        cmd1 = [
            "ffmpeg","-y",*common,
            "-i", input_mov, "-loop","1","-i", frame_png,
            "-filter_complex",
            f"[0:v]{base}[v];[1:v]{bor}[f];[v][f]overlay=x=0:y=0:format=auto,{palgen}",
            palette_path
        ]
        subprocess.run(cmd1, check=True)

        print(f"[roundify]  {tag} PASS 2/2 paletteuse → {out_gif}")
        cmd2 = [
            "ffmpeg","-y",*common,
            "-i", input_mov, "-loop","1","-i", frame_png, "-i", palette_path,
            "-filter_complex",
            f"[0:v]{base}[v];[1:v]{bor}[f];[v][f]overlay=x=0:y=0:format=auto[pre];[pre][2:v]{paluse}",
            "-loop", str(loop), out_gif
        ]
        subprocess.run(cmd2, check=True)

    try: size = os.path.getsize(out_gif)
    except OSError: size = 1 << 60
    elapsed = time.time() - t0
    print(f"[roundify]  {tag} size={size/1024/1024:.2f} MB  elapsed={elapsed:.1f}s")
    return size, elapsed

def maybe_gifsicle(path, level):
    if shutil.which("gifsicle") is None: return False
    tmp = path + ".tmp"
    print(f"[roundify]  gifsicle -O3 --lossy={level}")
    subprocess.run(["gifsicle","-O3",f"--lossy={level}",path,"-o",tmp], check=True)
    os.replace(tmp, path); return True

def main():
    ap = argparse.ArgumentParser(description="Border-only overlay GIF encoder with frame-budget search (×2/÷2).")
    ap.add_argument("inputs", nargs="+", help="Input .mov paths")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--width", type=int, default=800)
    ap.add_argument("--radius", type=int, default=25)
    ap.add_argument("--border", type=int, default=2)
    ap.add_argument("--border-color", default="128,128,128,255")
    ap.add_argument("--max-colors", type=int, default=128)
    ap.add_argument("--dither", choices=["bayer","none","sierra2_4a"], default="bayer")
    ap.add_argument("--bayer-scale", type=int, default=5)
    ap.add_argument("--loop", type=int, default=0)
    ap.add_argument("--target-mb", type=float, default=5.0, help="Size budget per GIF.")
    ap.add_argument("--max-fps", type=float, default=25.0, help="Upper cap on fps when converting frames→fps.")
    ap.add_argument("--frames-init", type=int, default=1000, help="Start frame budget.")
    ap.add_argument("--frames-min", type=int, default=64, help="Smallest frame budget to try when halving.")
    ap.add_argument("--frames-max", type=int, default=32768, help="Safety cap when doubling.")
    ap.add_argument("--verbose", action="store_true", help="ffmpeg progress (-stats).")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if budget not met.")
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        fail("ffmpeg/ffprobe not found in PATH")

    # default out-dir
    if args.out_dir:
        out_dir = args.out_dir
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(os.path.dirname(script_dir), "mov2gif", "gifs") if os.path.basename(script_dir) == "assets" \
                    else os.path.join(script_dir, "mov2gif", "gifs")
        out_dir = candidate if os.path.isdir(os.path.dirname(candidate)) else None
    os.makedirs(out_dir or ".", exist_ok=True)

    border_rgba = tuple(map(int, args.border_color.split(",")))
    budget_bytes = int(args.target_mb * 1024 * 1024)

    for inp in args.inputs:
        if not os.path.exists(inp):
            print(f"Skip (not found): {inp}", file=sys.stderr); continue

        duration = ffprobe_duration(inp)
        if not duration or duration <= 0:
            fail(f"Cannot determine duration for {inp}")
        sw, sh = ffprobe_size(inp)
        w, h = scaled_size(sw, sh, args.width)

        # prebuild the static frame at this size
        with tempfile.TemporaryDirectory() as td:
            frame_png = os.path.join(td, "frame.png")
            build_border_only(w, h, args.radius, args.border, border_rgba).save(frame_png)

            name = os.path.splitext(os.path.basename(inp))[0]
            out_path = os.path.join(out_dir or os.path.dirname(inp), f"{name}_rounded.gif")

            print(f"\n[roundify] === {name} — target ≤ {args.target_mb:.2f} MB — {w}x{h} — duration {duration:.2f}s ===")

            # ----- Phase A: start at frames_init; double until fail or frames_max -----
            frames = max(args.frames_min, args.frames_init)
            best = None  # (frames, size_bytes, fps)
            attempt = 0

            while frames <= args.frames_max:
                fps = min(args.max_fps, max(1.0, frames / duration))
                attempt += 1
                tag = f"[A{attempt:02d} frames={frames} fps≈{fps:.3f}]"
                size, _ = encode_once(
                    input_mov=inp, frame_png=frame_png, out_gif=out_path,
                    width_out=w, height_out=h, fps=fps,
                    max_colors=args.max_colors, dither=args.dither, bayer_scale=args.bayer_scale,
                    loop=args.loop, verbose=args.verbose, tag=tag
                )
                if size <= budget_bytes:
                    best = (frames, size, fps)
                    print(f"[roundify]  {tag} ✓ under budget ({size/1024/1024:.2f} MB), doubling…")
                    frames *= 2
                else:
                    print(f"[roundify]  {tag} ✗ over budget ({size/1024/1024:.2f} MB)")
                    break  # leave A-phase

            # If we never succeeded yet, Phase B: halve until we hit budget or frames_min
            if best is None:
                print("[roundify]  No success yet; halving until it fits…")
                frames = max(args.frames_min, args.frames_init // 2)
                attempt = 0
                while frames >= args.frames_min:
                    fps = min(args.max_fps, max(1.0, frames / duration))
                    attempt += 1
                    tag = f"[B{attempt:02d} frames={frames} fps≈{fps:.3f}]"
                    size, _ = encode_once(
                        input_mov=inp, frame_png=frame_png, out_gif=out_path,
                        width_out=w, height_out=h, fps=fps,
                        max_colors=args.max_colors, dither=args.dither, bayer_scale=args.bayer_scale,
                        loop=args.loop, verbose=args.verbose, tag=tag
                    )
                    if size <= budget_bytes:
                        best = (frames, size, fps)
                        print(f"[roundify]  {tag} ✓ hit budget ({size/1024/1024:.2f} MB)")
                        break
                    else:
                        print(f"[roundify]  {tag} ✗ over budget ({size/1024/1024:.2f} MB); halving…")
                        if frames == args.frames_min: break
                        frames = max(args.frames_min, frames // 2)

            # If Phase A had a success and last try in A failed, take the previous success (as requested)
            if best is not None:
                f_best, sz_best, fps_best = best
                print(f"[roundify] FINAL → frames={f_best}  fps≈{fps_best:.3f}  size={sz_best/1024/1024:.2f} MB  → {out_path}")
            else:
                # optional last squeeze
                if shutil.which("gifsicle"):
                    print("[roundify] Trying gifsicle compressions (20 then 40)…")
                    if maybe_gifsicle(out_path, 20):
                        sz = os.path.getsize(out_path)
                        if sz <= budget_bytes:
                            print(f"[roundify] FINAL (gifsicle20) → {sz/1024/1024:.2f} MB → {out_path}")
                            continue
                        if maybe_gifsicle(out_path, 40):
                            sz = os.path.getsize(out_path)
                            if sz <= budget_bytes:
                                print(f"[roundify] FINAL (gifsicle40) → {sz/1024/1024:.2f} MB → {out_path}")
                                continue
                print(f"[roundify] Could not hit budget; left last attempt at {out_path}")
                if args.strict: fail("Budget not met")

if __name__ == "__main__":
    main()
