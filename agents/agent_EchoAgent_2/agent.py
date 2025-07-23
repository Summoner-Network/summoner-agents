from summoner.client import SummonerClient
from summoner.protocol import Direction
from typing import Any, Union, Optional
import argparse, json
import asyncio
import uuid

# ---[ Queue ]---

# Initialized in setup()
message_buffer = None  

async def setup():
    global message_buffer
    message_buffer = asyncio.Queue()

# ---[ Agent & ID ]---

# Create Agent class as a SummonerClient subclass
class MyAgent(SummonerClient):
    def __init__(self, name: Optional[str] = None):
        super().__init__(name=name)
        # Get permnanent JSON id from file
        id_dict: dict = json.load(open("agents/agent_EchoAgent_2/id.json","r"))
        self.my_id = id_dict.get("uuid")

client = MyAgent(name="EchoAgent_1")

# ---[ Hooks ]---
@client.hook(direction=Direction.RECEIVE)
async def sign(msg: Any) -> Optional[dict]:
    if isinstance(msg, str) and msg.startswith("Warning:"):
        client.logger.warning(msg.replace("Warning:", "[From Server]"))
        return # None outputs are not passed to @receive handlers
    
    if not (isinstance(msg, dict) and "addr" in msg and "content" in msg):
        client.logger.info("[hook:recv] missing address/content")
        return # None outputs are not passed to @receive handlers
    
    client.logger.info(f"[hook:recv] {msg['addr']} passed validation")
    return msg

@client.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    client.logger.info(f"[hook:send] sign {client.my_id[:5]}")

    if isinstance(msg, str): msg = {"message": msg}
    if not isinstance(msg, dict): return
    
    # Sign the message
    msg.update({"from": client.my_id})
    return msg

# ---[ Receive and Send Handlers ]---
@client.receive(route="")
async def custom_receive(msg: Any) -> None:
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