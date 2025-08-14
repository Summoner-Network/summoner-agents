from summoner.client import SummonerClient
from db_models import Message
from db_sdk import Database
from pathlib import Path
from typing import Any
import argparse, json
import asyncio

client = SummonerClient(name="RecvAgent_0")

# Path to this agent's database file
db_path = Path(__file__).parent / f"{client.name}.db"
db = Database(db_path)

@client.receive(route="")
async def collect(msg: Any) -> None:
    if isinstance(msg, dict) and "remote_addr" in msg and "content" in msg:
        address = msg["remote_addr"]
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

    # Initialize the DB table
    client.loop.run_until_complete(Message.create_table(db))

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        # Cleanly close the connection on shutdown
        asyncio.run(db.close())
