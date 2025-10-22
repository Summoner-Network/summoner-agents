# Player.py
import os, sys, time, threading, asyncio, random
import pygame
from typing import Any, Dict
from summoner.client import SummonerClient
from summoner.protocol.process import Direction
from summoner.client import SummonerClient
import argparse

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

MAP_W, MAP_H = 1024, 768
PLAYER_RADIUS = 10
FPS = 60

BG = (15, 18, 24)
ME = (0, 200, 255)
OTHER = (0, 120, 180)
HUD = (230, 230, 230)

PID = f"p{random.randint(100000, 999999)}"
client = SummonerClient(name=f"GamePlayerAgent_0")

INPUT = {"w": False, "a": False, "s": False, "d": False}
SNAP: Dict[str, Any] = {"type": "world_state", "bounds": {"w": MAP_W, "h": MAP_H, "pr": PLAYER_RADIUS}, "players": [], "ts": None}
LOCK = threading.Lock()
RUNNING = True

@client.hook(Direction.RECEIVE)
async def rx_normalize(payload):
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], dict):
        inner = payload["content"]
        return inner.get("_payload", inner)
    return payload

@client.hook(Direction.SEND)
async def tx_stamp_pid(payload):
    if isinstance(payload, dict) and "pid" not in payload:
        payload["pid"] = PID
    return payload

@client.receive("gm/reply")
async def on_world(msg: dict):
    if not isinstance(msg, dict) or msg.get("type") != "world_state":
        return None
    with LOCK:
        SNAP = globals()["SNAP"]
        SNAP["ts"] = msg.get("ts")
        SNAP["bounds"] = msg.get("bounds", SNAP.get("bounds"))
        SNAP["players"] = msg.get("players", [])
    return None

@client.send("gm/tick")
async def tick():
    await asyncio.sleep(0.05)  # 20 Hz
    with LOCK:
        keys = dict(INPUT)
    return {"type": "tick", "ts": time.time(), "keys": keys}

def run_client():
    # avoid installing signal handlers in non-main thread
    if hasattr(client, "set_termination_signals"):
        client.set_termination_signals = lambda *a, **k: None
    asyncio.set_event_loop(asyncio.new_event_loop())
    client.run(host="127.0.0.1", port=8888, config_path=None)

def draw_circle(screen, color, x, y, r):
    pygame.draw.circle(screen, color, (int(x), int(y)), int(r))

def ui_loop():
    pygame.init()
    screen = pygame.display.set_mode((MAP_W, MAP_H))
    pygame.display.set_caption(f"Summoner Free-Roam — {PID}")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 22)

    while RUNNING:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return

        pressed = pygame.key.get_pressed()
        with LOCK:
            INPUT["w"] = bool(pressed[pygame.K_w] or pressed[pygame.K_UP])
            INPUT["a"] = bool(pressed[pygame.K_a] or pressed[pygame.K_LEFT])
            INPUT["s"] = bool(pressed[pygame.K_s] or pressed[pygame.K_DOWN])
            INPUT["d"] = bool(pressed[pygame.K_d] or pressed[pygame.K_RIGHT])
            snapshot = dict(SNAP)

        screen.fill(BG)

        # players
        for p in snapshot.get("players", []):
            color = ME if p.get("pid") == PID else OTHER
            draw_circle(screen, color, p["x"], p["y"], PLAYER_RADIUS)

        # HUD
        ts = snapshot.get("ts")
        text = f"PID {PID}   players={len(snapshot.get('players', []))}"
        if ts is not None:
            text += f"   t={ts:.2f}"
        screen.blit(font.render(text, True, HUD), (10, 10))

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    t = threading.Thread(target=run_client, name="summoner-client", daemon=True)
    t.start()
    try:
        ui_loop()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        RUNNING = False