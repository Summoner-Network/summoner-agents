# Player.py
import os, sys, time, threading, asyncio, json, argparse, math, random
import pygame
from typing import Any, Dict, Optional
from summoner.client import SummonerClient
from summoner.protocol.process import Direction

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

# ===== UI defaults =====
DEFAULT_WIN_W, DEFAULT_WIN_H = 600, 600
PLAYER_RADIUS = 10
FPS = 60

# Grass tile
TILE = 32
GRASS_A = (95, 159, 53)
GRASS_B = (106, 170, 60)

ME = (0, 200, 255)
OTHER = (0, 120, 180)
HUD = (235, 235, 235)

# Resolve paths relative to this file
HERE = os.path.dirname(os.path.abspath(__file__))

# ===== Global (filled in main) =====
PID = None  # will be set by identity loader

INPUT = {"w": False, "a": False, "s": False, "d": False}
SNAP: Dict[str, Any] = {"type": "world_state", "bounds": {"w": 10000, "h": 8000, "pr": PLAYER_RADIUS}, "players": [], "ts": None}
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
import secrets

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
    # short, unique-ish, file-name safe
    return f"p{secrets.randbelow(900000) + 100000}"

def load_or_create_identity(id_arg: Optional[str]) -> str:
    """
    Rules:
    - --id alias:
        * If HERE/alias.id exists -> reuse its content as the ID.
        * Else -> create HERE/alias.id with content 'alias' and use 'alias' as the ID.
    - No --id:
        * Generate a new random ID (e.g., p123456).
        * Create HERE/<id>.id with the same ID as content.
        * Always create a NEW file (ignore any old player_id.id).
    """
    # Aliased identity: alias maps to file <alias>.id, whose content is the real ID
    if id_arg:
        idfile = os.path.join(HERE, f"{id_arg}.id")
        existing = _read_text(idfile)
        if existing:
            return existing
        _write_text(idfile, id_arg)
        return id_arg

    # No alias: always mint a fresh ID and a *new* file named after it
    while True:
        new_id = _rand_id()
        idfile = os.path.join(HERE, f"{new_id}.id")
        if not os.path.exists(idfile):
            _write_text(idfile, new_id)
            return new_id
        # extremely unlikely collision; loop again

# ===== Pygame helpers =====
def make_grass_tile() -> pygame.Surface:
    surf = pygame.Surface((TILE, TILE))
    half = TILE // 2
    pygame.draw.rect(surf, GRASS_A, (0, 0, half, half))
    pygame.draw.rect(surf, GRASS_B, (half, 0, half, half))
    pygame.draw.rect(surf, GRASS_B, (0, half, half, half))
    pygame.draw.rect(surf, GRASS_A, (half, half, half, half))
    return surf

def draw_grass(screen: pygame.Surface, tile: pygame.Surface, cam_x: float, cam_y: float):
    w, h = screen.get_size()
    start_ix = int(math.floor(cam_x / TILE))
    start_iy = int(math.floor(cam_y / TILE))
    off_x = - (cam_x - start_ix * TILE)
    off_y = - (cam_y - start_iy * TILE)
    cols = w // TILE + 3
    rows = h // TILE + 3
    for r in range(rows):
        for c in range(cols):
            x = int(off_x + c * TILE)
            y = int(off_y + r * TILE)
            screen.blit(tile, (x, y))

def world_to_screen(px: float, py: float, cam_x: float, cam_y: float) -> tuple[int, int]:
    return int(px - cam_x), int(py - cam_y)

def find_me(players: list[dict]) -> Optional[dict]:
    for p in players:
        if p.get("pid") == PID:
            return p
    return None

# ===== UI loop (resizable window, camera follows player, optional avatar) =====
def ui_loop(avatar_path: Optional[str]):
    pygame.init()
    flags = pygame.RESIZABLE
    screen = pygame.display.set_mode((DEFAULT_WIN_W, DEFAULT_WIN_H), flags)
    pygame.display.set_caption(f"Summoner Free-Roam — {PID}")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 22)
    grass_tile = make_grass_tile()

    # Load avatar if provided; resolve relative to script folder
    my_avatar = None
    if avatar_path:
        apath = avatar_path
        if not os.path.isabs(apath):
            apath = os.path.join(HERE, apath)
        try:
            surf = pygame.image.load(apath).convert_alpha()
            size = max(PLAYER_RADIUS * 3, 24)
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

        # Background
        draw_grass(screen, grass_tile, cam_x, cam_y)

        # Players
        for p in players:
            sx, sy = world_to_screen(p["x"], p["y"], cam_x, cam_y)
            if p.get("pid") == PID and my_avatar is not None:
                screen.blit(my_avatar, my_avatar.get_rect(center=(sx, sy)))
            else:
                pygame.draw.circle(screen, ME if p.get("pid") == PID else OTHER, (sx, sy), PLAYER_RADIUS)

        # HUD with coordinates
        ts = snapshot.get("ts")
        if me is not None:
            coords = f"x={me['x']:.1f}  y={me['y']:.1f}"
        else:
            coords = "x=…  y=…"
        text = f"ID {PID}   players={len(players)}   {coords}"
        if ts is not None:
            text += f"   t={ts:.2f}"
        screen.blit(font.render(text, True, HUD), (10, 10))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()

# ===== Summoner agent code =====
client = SummonerClient(name=f"GamePlayerAgent_1")

# ----- Hooks -----
@client.hook(Direction.RECEIVE)
async def rx_normalize(payload: Any) -> Optional[dict]:
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], dict):
        inner = payload["content"]
        return inner.get("_payload", inner)
    return payload

@client.hook(Direction.SEND)
async def tx_stamp_pid(payload: Any) -> Optional[dict]:
    # hooks capture global PID (set in main before client.run())
    if isinstance(payload, dict) and "pid" not in payload:
        payload["pid"] = PID
    return payload

# ----- Routes -----
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

# ----- Summoner runner (background thread) -----
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
    parser = argparse.ArgumentParser(description="Run a Summoner Player with persistent identity and optional avatar.")
    parser.add_argument("--config", dest="config_path", required=False, help="Path to JSON config for the client.")
    parser.add_argument("--host", type=str, default=None, help="Server host (overrides config).")
    parser.add_argument("--port", type=int, default=None, help="Server port (overrides config).")
    parser.add_argument("--avatar", type=str, default=None, help="Path to a PNG with transparency (relative to this script or absolute).")
    parser.add_argument("--id", type=str, default=None, help="Persistent ID alias. If missing, uses/creates player_id.id.")
    args = parser.parse_args()

    # Load or create persistent ID
    PID = load_or_create_identity(args.id)
    client.logger.info(f"[Player] Using persistent ID: {PID}")
    client.name = f"Player_{PID}"  # make client name reflect the ID

    # Load config or fall back
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
        ui_loop(args.avatar)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        RUNNING = False
