from summoner.client import SummonerClient
from typing import Any, Union
import argparse, json
import asyncio

client = SummonerClient(name="EchoAgent_0")

# Initialized in setup()
message_buffer = None  

async def setup():
    global message_buffer
    message_buffer = asyncio.Queue()

@client.receive(route="")
async def custom_receive(msg: Any) -> None:
    
    if isinstance(msg, str) and msg.startswith("Warning:"):
        client.logger.warning(msg.replace("Warning:", "[From Server]"))

    if isinstance(msg, dict) and "addr" in msg and "content" in msg:
        address = msg["addr"]
        content = json.dumps(msg["content"])
        await message_buffer.put(content)
        client.logger.info(f"Buffered message from:(SocketAddress={address}).")

@client.send(route="")
async def custom_send() -> Union[dict, str]:
    content = await message_buffer.get()
    await asyncio.sleep(1)
    return json.loads(content)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.run_until_complete(setup())

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")