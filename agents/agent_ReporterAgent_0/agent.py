from summoner.client import SummonerClient
from typing import Any
import argparse, asyncio

client = SummonerClient(name="ReportAgent_0")

# Asyncio queue initialized in setup()
message_buffer = None  

async def setup():
    global message_buffer
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
    # Wait for the first message (blocks indefinitely if nothing arrives)
    first = await message_buffer.get()
    batch = [first]

    # After first message, wait 5 seconds to collect more
    await asyncio.sleep(5)

    while True:
        try:
            msg = message_buffer.get_nowait()
            batch.append(msg)
        except asyncio.QueueEmpty:
            break

    return "\n".join(batch)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.run_until_complete(setup())

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")