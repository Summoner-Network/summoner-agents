from summoner.client import SummonerClient
from summoner.protocol import Direction
from typing import Union, Optional
import argparse
import asyncio
import uuid

client = SummonerClient(name="SendAgent_1")

my_id = str(uuid.uuid4())

@client.hook(direction=Direction.SEND)
async def sign(msg: Union[dict, str]) -> Optional[Union[dict, str]]:
    client.logger.info(f"[hook:send] sign {my_id[:5]}")
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict): 
        return
    msg.update({"from": my_id})
    return msg

@client.send(route="")
async def custom_send() -> str:
    await asyncio.sleep(1)
    return "Hello Server!"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")