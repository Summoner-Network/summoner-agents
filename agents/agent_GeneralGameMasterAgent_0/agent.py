import json
import time
from summoner.client import SummonerClient
from typing import Any
import argparse, asyncio

client = SummonerClient(name="GeneralGameMasterAgent_0")

# Asyncio queue initialized in setup()
message_buffer = None
FPS = 60
FRAME = -1
TIME = 0

def now():
    return time.time_ns()

def ns_to_sec(ns: int) -> float:
    return ns / 1e9

async def setup():
    global message_buffer
    global FPS, FRAME, TIME
    message_buffer = asyncio.Queue()

@client.receive(route="")
async def custom_receive(msg: Any) -> None:
    content: str = msg["content"] if isinstance(msg, dict) and "content" in msg else msg
    if isinstance(content, str):
        await message_buffer.put(content)
        tag = "\r[From server]" if content.startswith("Warning:") else "\r[Received]"
        print(tag, content, flush=True)

@client.send(route="")
async def custom_send() -> Any:
    return await drain_buffer()


async def drain_buffer():
    # Declare that we are modifying the global variables
    global FRAME, TIME
    
    batch = {}
    try:
        # Attempt to get the first message without waiting
        first = await message_buffer.get_nowait()
        batch = {
            "frameNumber": FRAME + 1,
            "deltaEvents": [first],
        }
    except asyncio.QueueEmpty:
        # If queue is empty, sleep to maintain FPS and prepare an empty batch
        await asyncio.sleep(max(0, (1 / FPS) - ns_to_sec(now() - TIME)))
        batch = {
            "frameNumber": FRAME + 1,
            "deltaEvents": [],
        }

    # This loop now correctly drains all remaining messages
    while True:
        try:
            msg = message_buffer.get_nowait()
            batch["deltaEvents"].append(msg)
        except asyncio.QueueEmpty:
            # When the queue is empty, finalize the batch and break the loop
            batch["deltaTiming"] = now() - TIME
            break
    
    # These lines are now correctly placed AFTER the loop finishes
    FRAME = batch["frameNumber"]
    TIME = now() # Update TIME to the current timestamp for the next frame's calculation

    return json.dumps(batch)

    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.run_until_complete(setup())
    TIME = time.time_ns()
    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")