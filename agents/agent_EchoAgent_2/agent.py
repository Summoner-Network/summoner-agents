from summoner.client import SummonerClient
from summoner.protocol import Direction
from typing import Any, Union, Optional
import argparse, json
import asyncio

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

agent = MyAgent(name="EchoAgent_1")

# ---[ Hooks ]---
@agent.hook(direction=Direction.RECEIVE)
async def sign(msg: Any) -> Optional[dict]:
    if isinstance(msg, str) and msg.startswith("Warning:"):
        agent.logger.warning(msg.replace("Warning:", "[From Server]"))
        return # None outputs are not passed to @receive handlers
    
    if not (isinstance(msg, dict) and "addr" in msg and "content" in msg):
        agent.logger.info("[hook:recv] missing address/content")
        return # None outputs are not passed to @receive handlers
    
    agent.logger.info(f"[hook:recv] {msg['addr']} passed validation")
    return msg

@agent.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    agent.logger.info(f"[hook:send] sign {agent.my_id[:5]}")

    if isinstance(msg, str): msg = {"message": msg}
    if not isinstance(msg, dict): return
    
    # Sign the message
    msg.update({"from": agent.my_id})
    return msg

# ---[ Receive and Send Handlers ]---
@agent.receive(route="")
async def custom_receive(msg: Any) -> None:
    address = msg["addr"]
    content = json.dumps(msg["content"])
    await message_buffer.put(content)
    agent.logger.info(f"Buffered message from:(SocketAddress={address}).")

@agent.send(route="")
async def custom_send() -> Union[dict, str]:
    content = await message_buffer.get()
    await asyncio.sleep(1)
    return json.loads(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner agent with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the agent (e.g., --config configs/agent_config.json)')
    args = parser.parse_args()

    agent.loop.run_until_complete(setup())

    agent.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/agent_config.json")