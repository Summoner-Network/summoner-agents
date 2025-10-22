import asyncio, time, math, random
from typing import Dict, Any, Optional
from summoner.client import SummonerClient
from summoner.protocol.process import Direction
import argparse

# ===== World constants =====
MAP_W, MAP_H = 1024, 768
PLAYER_RADIUS = 10
PLAYER_SPEED = 4.0         # px per step (pre-diagonal normalization)

SIM_STEP_MS = 16.6667      # ~60 Hz
BROADCAST_EVERY_MS = 50.0  # 20 Hz

# In-memory world
class Player:
    __slots__ = ("pid", "x", "y", "vx", "vy", "keys")
    def __init__(self, pid: str):
        self.pid = pid
        self.x = random.uniform(32, MAP_W - 32)
        self.y = random.uniform(32, MAP_H - 32)
        self.vx = 0.0
        self.vy = 0.0
        self.keys = {"w": False, "a": False, "s": False, "d": False}

players: Dict[str, Player] = {}

def clamp(v, lo, hi): return max(lo, min(hi, v))

def apply_inputs(dt_ms: float):
    for p in players.values():
        dx = (-1 if p.keys["a"] else 0) + (1 if p.keys["d"] else 0)
        dy = (-1 if p.keys["w"] else 0) + (1 if p.keys["s"] else 0)
        if dx != 0 and dy != 0:
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

client = SummonerClient(name="GameMasterAgent_0")

# Normalize server envelopes → bare dict
@client.hook(Direction.RECEIVE)
async def rx_normalize(payload: Any) -> Optional[dict]:
    if isinstance(payload, dict) and "content" in payload and isinstance(payload["content"], dict):
        inner = payload["content"]
        return inner.get("_payload", inner)
    return payload

@client.receive("gm/tick")
async def on_tick(msg: dict) -> None:
    if not isinstance(msg, dict) or msg.get("type") != "tick":
        return None
    pid = msg.get("pid")
    if not pid:
        return None
    p = players.get(pid)
    if p is None:
        p = Player(pid)
        players[pid] = p
        client.logger.info(f"[GM] join {pid} at ({p.x:.1f},{p.y:.1f})")
    keys = msg.get("keys") or {}
    p.keys["w"] = bool(keys.get("w"))
    p.keys["a"] = bool(keys.get("a"))
    p.keys["s"] = bool(keys.get("s"))
    p.keys["d"] = bool(keys.get("d"))
    return None

@client.send("gm/reply")
async def send_world() -> dict:
    # 20 Hz broadcast cadence; receiver ticks can be higher
    await asyncio.sleep(BROADCAST_EVERY_MS / 1000.0)
    return world_state()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.create_task(sim_loop())

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
