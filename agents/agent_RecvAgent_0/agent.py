from summoner.client import SummonerClient
from pathlib import Path
from db_models import Message 
import argparse
import asyncio
import json

client = SummonerClient(name="RecvAgent_0")

# path to the agent’s own database file
db_path = Path(__file__).parent / f"{client.name}.db"

@client.receive(route="")
async def collect(msg):
    if isinstance(msg, dict) and "content" in msg and "addr" in msg:
        addr = msg["addr"]
        content = msg["content"]
        
        addr = addr if isinstance(addr, str) else json.dumps(addr)
        content = content if isinstance(content, str) else json.dumps(content)
        
        client.logger.info(f"Received message from Client @(SocketAddress={addr})")

        await Message.insert(db_path, addr=addr, content=content)
        messages = await Message.filter(db_path, filter={"addr": addr})
        
        client.logger.info(f"Client @(SocketAddress={addr}) has now {len(messages)} messages stored.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Initialize the DB table
    asyncio.get_event_loop().run_until_complete(Message.create_table(db_path))
    
    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
