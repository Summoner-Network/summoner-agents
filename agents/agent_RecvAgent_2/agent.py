from summoner.client import SummonerClient
from summoner.protocol import Direction
from db_models import Message, Validation, BannedAddress
from db_sdk import Database
from typing import Any, Optional
from pathlib import Path
import argparse, json
import asyncio

client = SummonerClient(name="RecvAgent_2")

db_path = Path(__file__).parent / f"{client.name}.db"
db = Database(db_path)

async def setup():
    await Message.create_table(db)
    await Validation.create_table(db)
    await BannedAddress.create_table(db)

    # indexes for faster lookups
    await Validation.create_index(db, "idx_validations_address", ["address"])
    await Message.create_index(db, "idx_messages_sender_id", ["sender_id"])
    await BannedAddress.create_index(db, "idx_banned_address", ["address"], unique=True)


async def should_ban(address: str) -> bool:
    rows = await Validation.find(db, where={"address": address})
    total = len(rows)
    if total == 0:
        return False
    good = sum(r["is_valid"] for r in rows)
    bad = total - good
    # Ban if ≥ 20 bad signals and < 50% good
    return bad >= 20 and (good / total) < 0.5


@client.hook(direction=Direction.RECEIVE)
async def sign(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        client.logger.info("[hook:recv] missing address/content")
        return

    address = msg["remote_addr"]
    banned = await BannedAddress.find(db, where={"address": address})
    answer = "\033[91mTrue\033[0m" if bool(banned) else "\033[92mFalse\033[0m"
    client.logger.info(f"[hook:recv] {address} \033[93m-> Banned?\033[0m {answer}")
    if banned:
        return

    content = msg["content"]
    is_valid = int(isinstance(content, dict) and "from" in content and 
                   isinstance(content["from"], str) and len(content["from"]) > 0)

    # Record validation event
    if is_valid:
        await Validation.insert(db, address=address, sender_id=content["from"], is_valid=is_valid)

    else:
        await Validation.insert(db, address=address, is_valid=is_valid)
        client.logger.info(f"[hook:recv] {address} \033[95minvalid -> checking if ban is required...\033[0m")
        if await should_ban(address):
            client.logger.info(f"[hook:recv] {address} \033[91mhas been banned\033[0m")
            await BannedAddress.insert_or_ignore(db, address=address)
        return

    # Valid message: strip sender id and forward
    from_id = content.pop("from")
    client.logger.info(f"[hook:recv] {address} valid, id={from_id[:5]}...")
    return {"from": from_id, "content": content}


@client.receive(route="")
async def collect(msg: dict) -> None:
    from_id = msg.get("from")
    content = msg.get("content")
    from_id = from_id if isinstance(from_id, str) else json.dumps(from_id)
    content = content if isinstance(content, str) else json.dumps(content)

    client.logger.info(f"Received message from Agent @(id={from_id[:5]}...)")
    await Message.insert(db, sender_id=from_id, content=content)
    messages = await Message.find(db, where={"sender_id": from_id})
    client.logger.info(f"Agent @(id={from_id[:5]}...) has now {len(messages)} messages stored.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Initialize the DB tables and indexes
    client.loop.run_until_complete(setup())

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        asyncio.run(db.close())
