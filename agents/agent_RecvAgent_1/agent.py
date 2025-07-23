from summoner.client import SummonerClient
from summoner.protocol import Direction
from db_models import Message
from db_sdk import Database
from typing import Any, Optional
from pathlib import Path
from typing import Any
import argparse, json
import asyncio

client = SummonerClient(name="RecvAgent_1")

db_path = Path(__file__).parent / f"{client.name}.db"
db = Database(db_path)

@client.hook(direction=Direction.RECEIVE)
async def sign(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "addr" in msg and "content" in msg):
        client.logger.info("[hook:recv] missing address/content")
        return # None outputs are not passed to @receive handlers
    client.logger.info(f"[hook:recv] {msg['addr']} passed validation")
    return msg

@client.receive(route="")
async def collect(msg: Any) -> None:
    address = msg["addr"]
    content = msg["content"]
    address = address if isinstance(address, str) else json.dumps(address)
    content = content if isinstance(content, str) else json.dumps(content)

    client.logger.info(f"Received message from Client @(SocketAddress={address}).")
    await Message.insert(db, address=address, content=content)
    messages = await Message.find(db, where={"address": address})
    client.logger.info(f"Client @(SocketAddress={address}) has now {len(messages)} messages stored.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.run_until_complete(Message.create_table(db))

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        asyncio.run(db.close())
