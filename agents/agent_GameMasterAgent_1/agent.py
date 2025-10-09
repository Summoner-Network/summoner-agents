# GameMaster.py
import asyncio, time, math, random
from typing import Dict, Any
from summoner.client import SummonerClient
from summoner.protocol.process import Direction
import argparse

MAP_W, MAP_H = 10000, 8000
PLAYER_RADIUS = 10
PLAYER_SPEED = 4.0

SIM_STEP_MS = 16.6667
BROADCAST_EVERY_MS = 50.0

# --- NEW: spawn settings ---
SPAWN_CX, SPAWN_CY = MAP_W / 2, MAP_H / 2
SPAWN_RING_R = 140.0     # players start within ~140 px of each other
SPAWN_JITTER = 18.0      # small randomization to avoid exact overlap

client = SummonerClient(name="GameMasterAgent_1")

@client.hook(Direction.RECEIVE, priority=(0,))
async def rx_normalize(payload):
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], dict):
        inner = payload["content"]
        return inner.get("_payload", inner)
    return payload

class Player:
    __slots__ = ("pid", "x", "y", "vx", "vy", "keys")
    def __init__(self, pid: str, idx: int):
        self.pid = pid
        # --- NEW: place new players around a small ring near center ---
        if idx == 0:
            base_x, base_y = SPAWN_CX, SPAWN_CY
        else:
            angle = (idx * 137.508) * math.pi / 180.0  # golden-ish angle
            base_x = SPAWN_CX + math.cos(angle) * SPAWN_RING_R
            base_y = SPAWN_CY + math.sin(angle) * SPAWN_RING_R
        self.x = max(PLAYER_RADIUS, min(MAP_W - PLAYER_RADIUS, base_x + random.uniform(-SPAWN_JITTER, SPAWN_JITTER)))
        self.y = max(PLAYER_RADIUS, min(MAP_H - PLAYER_RADIUS, base_y + random.uniform(-SPAWN_JITTER, SPAWN_JITTER)))

        self.vx = 0.0
        self.vy = 0.0
        self.keys = {"w": False, "a": False, "s": False, "d": False}

players: Dict[str, Player] = {}

def clamp(v, lo, hi): return max(lo, min(hi, v))

def apply_inputs(dt_ms: float):
    for p in players.values():
        dx = (-1 if p.keys["a"] else 0) + (1 if p.keys["d"] else 0)
        dy = (-1 if p.keys["w"] else 0) + (1 if p.keys["s"] else 0)
        if dx and dy:
            inv = 1 / math.sqrt(2.0)
            dx *= inv; dy *= inv
        p.vx = dx * PLAYER_SPEED
        p.vy = dy * PLAYER_SPEED
        p.x = clamp(p.x + p.vx, PLAYER_RADIUS, MAP_W - PLAYER_RADIUS)
        p.y = clamp(p.y + p.vy, PLAYER_RADIUS, MAP_H - PLAYER_RADIUS)

def world_state() -> Dict[str, Any]:
    return {
        "type": "world_state",
        "ts": time.time(),
        "bounds": {"w": MAP_W, "h": MAP_H, "pr": PLAYER_RADIUS},
        "players": [{"pid": p.pid, "x": p.x, "y": p.y} for p in players.values()],
    }

@client.receive("gm/tick")
async def on_tick(msg: dict):
    if not isinstance(msg, dict) or msg.get("type") != "tick":
        return None
    pid = msg.get("pid")
    if not pid:
        return None
    p = players.get(pid)
    if p is None:
        p = Player(pid, idx=len(players))
        players[pid] = p
        client.logger.info(f"[GM] join {pid} at ({p.x:.1f},{p.y:.1f})")
    keys = msg.get("keys") or {}
    p.keys["w"] = bool(keys.get("w")); p.keys["a"] = bool(keys.get("a"))
    p.keys["s"] = bool(keys.get("s")); p.keys["d"] = bool(keys.get("d"))
    return None

@client.send("gm/reply")
async def send_world():
    await asyncio.sleep(BROADCAST_EVERY_MS / 1000.0)
    return world_state()

async def sim_loop():
    acc = 0.0
    last_ms = time.perf_counter() * 1000.0
    while True:
        now_ms = time.perf_counter() * 1000.0
        dt = now_ms - last_ms
        last_ms = now_ms
        acc += dt
        while acc >= SIM_STEP_MS:
            apply_inputs(SIM_STEP_MS)
            acc -= SIM_STEP_MS
        await asyncio.sleep(0.001)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.create_task(sim_loop())

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")


