import os, sys, time, threading, asyncio, json, argparse, math, random, secrets
import pygame
from typing import Any, Dict, Optional

from summoner.client import SummonerClient
from summoner.protocol.process import Direction

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# ===== UI defaults =====
DEFAULT_WIN_W, DEFAULT_WIN_H = 600, 600
PLAYER_RADIUS = 10
FPS = 60

# Tile/world
TILE = 32  # draw-sized tile
# Base greens (opaque; no alpha ops anywhere)
GRASS_A = (95, 159, 53)
GRASS_B = (106, 170, 60)

ME = (0, 200, 255)
OTHER = (0, 120, 180)
HUD = (235, 235, 235)

# Resolve paths relative to this file
HERE = os.path.dirname(os.path.abspath(__file__))

# ===== Global (filled in main) =====
PID = None  # set by identity loader
client = SummonerClient(name=f"GamePlayerAgent_2")

INPUT = {"w": False, "a": False, "s": False, "d": False}
SNAP: Dict[str, Any] = {
    "type": "world_state",
    "bounds": {"w": 10000, "h": 8000, "pr": PLAYER_RADIUS},
    "players": [],
    "ts": None
}
LOCK = threading.Lock()
RUNNING = True

# ===== Default client config (can be overridden by --config) =====
DEFAULT_PLAYER_CONFIG: Dict[str, Any] = {
    "host": None,
    "port": None,
    "logger": {
        "log_level": "INFO",
        "enable_console_log": True,
        "console_log_format": "\u001b[92m%(asctime)s\u001b[0m - \u001b[94m%(name)s\u001b[0m - %(levelname)s - %(message)s",
        "enable_file_log": True,
        "enable_json_log": False,
        "log_file_path": "logs/",
        "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "max_file_size": 1000000,
        "backup_count": 3,
        "date_format": "%Y-%m-%d %H:%M:%S.%f",
        "log_keys": None
    },
    "hyper_parameters": {
        "receiver": {"max_bytes_per_line": 65536, "read_timeout_seconds": None},
        "sender": {
            "concurrency_limit": 16, "batch_drain": False, "queue_maxsize": 128,
            "event_bridge_maxsize": 2000, "max_worker_errors": 3
        },
        "reconnection": {
            "retry_delay_seconds": 3, "primary_retry_limit": 5,
            "default_host": "127.0.0.1", "default_port": 8888, "default_retry_limit": 3
        }
    }
}

# ===== Identity persistence (collision-proof) =====
def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None

def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

def _rand_id() -> str:
    return f"p{secrets.randbelow(900000) + 100000}"

def load_or_create_identity(id_arg: Optional[str]) -> str:
    """
    --id alias:
      if HERE/alias.id exists -> reuse its content as the ID,
      else create HERE/alias.id containing 'alias' and use 'alias'.
    No --id:
      mint new random ID and create HERE/<id>.id with that content.
    """
    if id_arg:
        idfile = os.path.join(HERE, f"{id_arg}.id")
        existing = _read_text(idfile)
        if existing:
            return existing
        _write_text(idfile, id_arg)
        return id_arg

    while True:
        new_id = _rand_id()
        idfile = os.path.join(HERE, f"{new_id}.id")
        if not os.path.exists(idfile):
            _write_text(idfile, new_id)
            return new_id

# ===== World seed persistence =====
SEED_FILE = os.path.join(HERE, "world_seed.txt")

def load_or_create_world_seed(seed_arg: Optional[str]) -> str:
    if seed_arg and seed_arg.strip():
        seed = seed_arg.strip()
        with open(SEED_FILE, "w", encoding="utf-8") as f:
            f.write(seed + "\n")
        return seed

    if os.path.exists(SEED_FILE):
        try:
            with open(SEED_FILE, "r", encoding="utf-8") as f:
                existing = f.read().strip()
                if existing:
                    return existing
        except Exception:
            pass

    seed = f"world-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}"
    with open(SEED_FILE, "w", encoding="utf-8") as f:
        f.write(seed + "\n")
    return seed

# ===== Hooks =====
@client.hook(Direction.RECEIVE)
async def rx_normalize(payload: Any) -> Optional[dict]:
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], dict):
        inner = payload["content"]
        return inner.get("_payload", inner)
    return payload

@client.hook(Direction.SEND)
async def tx_stamp_pid(payload: Any) -> Optional[dict]:
    if isinstance(payload, dict) and "pid" not in payload:
        payload["pid"] = PID
    return payload

# ===== Routes =====
@client.receive("gm/reply")
async def on_world(msg: dict) -> None:
    if not isinstance(msg, dict) or msg.get("type") != "world_state":
        return None
    with LOCK:
        SNAP = globals()["SNAP"]
        SNAP["ts"] = msg.get("ts")
        if "bounds" in msg:  SNAP["bounds"] = msg["bounds"]
        if "players" in msg: SNAP["players"] = msg["players"]
    return None

@client.send("gm/tick")
async def tick() -> dict:
    await asyncio.sleep(0.05)  # 20 Hz
    with LOCK:
        keys = dict(INPUT)
    return {"type": "tick", "ts": time.time(), "keys": keys}

# ===== Seeded opaque grass (no alpha / no blending) =====

def _fnv1a32(s: str) -> int:
    """Stable 32-bit hash for (seed, tileX, tileY)."""
    h = 2166136261
    for b in s.encode("utf-8"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h

def _tile_shade(seed: str, ix: int, iy: int) -> float:
    """Deterministic 0..1 from seed + tile index."""
    h = _fnv1a32(f"{seed}|{ix}|{iy}")
    # map to [0,1]
    return ((h >> 8) & 0xFFFFFF) / 0xFFFFFF

def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )

def draw_grass_seeded(screen: pygame.Surface, seed: str, cam_x: float, cam_y: float):
    """
    Draw a 2×2 checker inside each tile using **opaque** fills only.
    """
    w, h = screen.get_size()
    start_ix = int(math.floor(cam_x / TILE))
    start_iy = int(math.floor(cam_y / TILE))
    off_x = - (cam_x - start_ix * TILE)
    off_y = - (cam_y - start_iy * TILE)
    cols = w // TILE + 3
    rows = h // TILE + 3

    for r in range(rows):
        for c in range(cols):
            ix = start_ix + c
            iy = start_iy + r
            x = int(off_x + c * TILE)
            y = int(off_y + r * TILE)

            t = _tile_shade(seed, ix, iy)  # 0..1
            # Limit variation to a gentle band around the base colors
            t_small = 0.25 * (t - 0.5)  # [-0.125..+0.125]
            cA = _mix(GRASS_A, GRASS_B, 0.5 + t_small)
            cB = _mix(GRASS_B, GRASS_A, 0.5 - t_small)

            half = TILE // 2
            # 2×2 checker, pure RGB fills
            pygame.draw.rect(screen, cA, (x, y, half, half))
            pygame.draw.rect(screen, cB, (x + half, y, half, half))
            pygame.draw.rect(screen, cB, (x, y + half, half, half))
            pygame.draw.rect(screen, cA, (x + half, y + half, half, half))

# --- Seeded grass with per-tile cache (opaque RGB) ---

class TileCache:
    """
    LRU cache of TILE×TILE pre-rendered grass tiles.
    Keyed by (seed, ix, iy). Keeps surfaces fully opaque (no alpha).
    """
    def __init__(self, cap: int = 4096):
        self.cap = cap
        self.store: dict[tuple[str, int, int], pygame.Surface] = {}
        self.order: list[tuple[str, int, int]] = []  # simple FIFO/LRU

    def get(self, seed: str, ix: int, iy: int) -> pygame.Surface:
        key = (seed, ix, iy)
        surf = self.store.get(key)
        if surf is not None:
            return surf
        surf = self._make_tile(seed, ix, iy)
        self.store[key] = surf
        self.order.append(key)
        if len(self.order) > self.cap:
            old = self.order.pop(0)
            self.store.pop(old, None)
        return surf

    def _make_tile(self, seed: str, ix: int, iy: int) -> pygame.Surface:
        """
        Build one TILE×TILE pixel-art grass tile:
        - Two nearby green shades picked per tile (seeded),
        - 4×4 Bayer dithering to distribute bright/dark pixels,
        - A few single-pixel 'blade' flecks,
        - 100% opaque RGB, no alpha or blending flags.
        """
        # --- seeded per-tile PRNG ---
        rng = random.Random(_fnv1a32(f"{seed}|{ix}|{iy}|bayer"))

        # Gentle brightness variation per tile
        t = _tile_shade(seed, ix, iy)          # 0..1
        mid_mix = 0.45 + 0.10 * (t - 0.5)      # around 0.45..0.55
        base_mid = _mix(GRASS_A, GRASS_B, mid_mix)

        # Create two close shades around that midpoint (A=dark, B=light)
        def clamp(v): return max(0, min(255, v))
        def tint(col, dv):
            return (clamp(col[0] + dv), clamp(col[1] + dv), clamp(col[2] + dv))

        # ±15 variance range; tweak to increase/decrease pixel contrast
        var = 15
        dark = tint(base_mid, -var - rng.randint(0, 4))
        lite = tint(base_mid, +var + rng.randint(0, 4))

        # 4×4 Bayer threshold matrix (values 0..15)
        B4 = (
            ( 0,  8,  2, 10),
            (12,  4, 14,  6),
            ( 3, 11,  1,  9),
            (15,  7, 13,  5),
        )
        # A per-tile threshold (0..16) controls how much 'lite' shows up
        # Lower threshold → fewer lite pixels; higher → more lite pixels
        # Seed it mildly by t and a random wobble
        threshold = max(4, min(12, int(8 + (t - 0.5) * 8 + rng.randint(-2, 2))))

        # Build opaque surface and lock for per-pixel writes
        surf = pygame.Surface((TILE, TILE)).convert()
        surf.lock()
        try:
            # Dithered fill: choose lite or dark by Bayer index
            for y in range(TILE):
                row = B4[y & 3]
                for x in range(TILE):
                    if row[x & 3] < threshold:
                        surf.set_at((x, y), lite)
                    else:
                        surf.set_at((x, y), dark)

            # A few tiny blades: 1–2 px bright flecks, very sparse (no flowers)
            flecks = rng.randint(4, 8)
            blade = tint(lite, +6)  # just a touch brighter than 'lite'
            for _ in range(flecks):
                x = rng.randrange(0, TILE)
                y = rng.randrange(0, TILE)
                surf.set_at((x, y), blade)
                if rng.random() < 0.3 and y+1 < TILE:
                    surf.set_at((x, y+1), blade)  # small 2px blade now and then
        finally:
            surf.unlock()

        return surf

def draw_grass_seeded_cached(screen: pygame.Surface, cache: TileCache, seed: str,
                             cam_x: float, cam_y: float) -> None:
    """
    Draw visible TILE-grid area using cached TILE×TILE surfaces.
    """
    w, h = screen.get_size()
    start_ix = int(math.floor(cam_x / TILE))
    start_iy = int(math.floor(cam_y / TILE))
    off_x = - (cam_x - start_ix * TILE)
    off_y = - (cam_y - start_iy * TILE)
    cols = w // TILE + 3
    rows = h // TILE + 3

    # Fill every visible tile from cache
    for r in range(rows):
        for c in range(cols):
            ix = start_ix + c
            iy = start_iy + r
            x = int(off_x + c * TILE)
            y = int(off_y + r * TILE)
            screen.blit(cache.get(seed, ix, iy), (x, y))

# ===== Helpers =====
def world_to_screen(px: float, py: float, cam_x: float, cam_y: float) -> tuple[int, int]:
    return int(px - cam_x), int(py - cam_y)

def find_me(players: list[dict]) -> Optional[dict]:
    for p in players:
        if p.get("pid") == PID:
            return p
    return None

# ===== UI loop (resizable window, camera follows player, optional avatar) =====
def ui_loop(avatar_path: Optional[str], world_seed: str):
    pygame.init()
    flags = pygame.RESIZABLE  # no DOUBLEBUF/alpha tricks
    screen = pygame.display.set_mode((DEFAULT_WIN_W, DEFAULT_WIN_H), flags)
    pygame.display.set_caption(f"Summoner Free-Roam — {PID}")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 22)

    tile_cache = TileCache(cap=4096)

    # Avatar loader: same stable path that worked for you before
    my_avatar = None
    if avatar_path:
        apath = avatar_path if os.path.isabs(avatar_path) else os.path.join(HERE, avatar_path)
        try:
            # Keep per-pixel alpha exactly as in your working version
            surf = pygame.image.load(apath).convert_alpha()
            size = max(PLAYER_RADIUS * 5, 24)
            my_avatar = pygame.transform.smoothscale(surf, (size, size))
        except Exception as e:
            print(f"[Player] Could not load avatar '{apath}': {e}")

    win_w, win_h = screen.get_size()
    cam_x, cam_y = 0.0, 0.0

    while RUNNING:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            elif event.type == pygame.VIDEORESIZE:
                win_w, win_h = event.w, event.h
                screen = pygame.display.set_mode((win_w, win_h), flags)

        pressed = pygame.key.get_pressed()
        with LOCK:
            INPUT["w"] = bool(pressed[pygame.K_w] or pressed[pygame.K_UP])
            INPUT["a"] = bool(pressed[pygame.K_a] or pressed[pygame.K_LEFT])
            INPUT["s"] = bool(pressed[pygame.K_s] or pressed[pygame.K_DOWN])
            INPUT["d"] = bool(pressed[pygame.K_d] or pressed[pygame.K_RIGHT])
            snapshot = dict(SNAP)

        bounds = snapshot.get("bounds", {"w": 10000, "h": 8000, "pr": PLAYER_RADIUS})
        players = snapshot.get("players", [])
        me = find_me(players)

        if me is not None:
            target_cx, target_cy = me["x"], me["y"]
        else:
            target_cx, target_cy = bounds["w"] * 0.5, bounds["h"] * 0.5

        cam_x = max(0.0, min(target_cx - win_w / 2.0, bounds["w"] - win_w))
        cam_y = max(0.0, min(target_cy - win_h / 2.0, bounds["h"] - win_h))

        # Draw seeded grass (pure RGB fills only)
        # draw_grass_seeded(screen, world_seed, cam_x, cam_y)

        draw_grass_seeded_cached(screen, tile_cache, world_seed, cam_x, cam_y)

        # Players
        for p in players:
            sx, sy = world_to_screen(p["x"], p["y"], cam_x, cam_y)
            if p.get("pid") == PID and my_avatar is not None:
                screen.blit(my_avatar, my_avatar.get_rect(center=(sx, sy)))
            else:
                pygame.draw.circle(screen, ME if p.get("pid") == PID else OTHER, (sx, sy), PLAYER_RADIUS)

        # HUD with coordinates (no backgrounds, just text)
        ts = snapshot.get("ts")
        coords = f"x={me['x']:.1f}  y={me['y']:.1f}" if me else "x=…  y=…"
        text = f"ID {PID}   players={len(players)}   {coords}   seed='{world_seed}'"
        if ts is not None:
            text += f"   t={ts:.2f}"
        screen.blit(font.render(text, True, HUD), (10, 10))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

# ===== Summoner runner (background thread) =====
def run_client(host: Optional[str], port: Optional[int], config_path: Optional[str], config_dict: Dict[str, Any]):
    # Avoid installing signal handlers in a non-main thread
    if hasattr(client, "set_termination_signals"):
        client.set_termination_signals = lambda *a, **k: None
    asyncio.set_event_loop(asyncio.new_event_loop())

    effective_cfg = dict(config_dict)
    hp = dict(effective_cfg.get("hyper_parameters", {}))
    effective_cfg["hyper_parameters"] = hp

    client.run(
        host=host if host is not None else "127.0.0.1",
        port=port if port is not None else 8888,
        config_path=config_path,
        config_dict=effective_cfg if config_path is None else None,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner Player with persistent identity, seeded grass, and optional avatar.")
    parser.add_argument("--config", dest="config_path", required=False, help="Path to JSON config for the client.")
    parser.add_argument("--host", type=str, default=None, help="Server host (overrides config).")
    parser.add_argument("--port", type=int, default=None, help="Server port (overrides config).")
    parser.add_argument("--avatar", type=str, default=None, help="Path to a PNG with transparency (relative/absolute).")
    parser.add_argument("--id", type=str, default=None, help="Persistent ID alias. If missing, a new <id>.id is created.")
    parser.add_argument("--seed", type=str, default=None, help="Deterministic world appearance seed (stored in world_seed.txt).")
    args = parser.parse_args()

    # Identity
    PID = load_or_create_identity(args.id)
    client.logger.info(f"[Player] Using persistent ID: {PID}")
    client.name = f"Player_{PID}"

    # Seed
    world_seed = load_or_create_world_seed(args.seed)

    # Config
    if args.config_path:
        try:
            with open(args.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[Player] Failed to load config {args.config_path}: {e}")
            cfg = DEFAULT_PLAYER_CONFIG
    else:
        cfg = DEFAULT_PLAYER_CONFIG

    # Start Summoner client in background
    t = threading.Thread(
        target=run_client, name="summoner-client", daemon=True,
        args=(args.host, args.port, args.config_path, cfg)
    )
    t.start()

    try:
        ui_loop(args.avatar, world_seed)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        RUNNING = False
