import argparse
import asyncio
import os, json
from pathlib import Path
from typing import Any, Optional
from aioconsole import aprint

from summoner.client import SummonerClient
from db_sdk import Database
from db_models import Message


# ======================================================================
# SECTION 1: DATABASE (db_sdk)
# ----------------------------------------------------------------------
# - Parse optional DB paths for send/receive.
# - Resolve them (relative paths live next to this file).
# - Create db_sdk Database instances.
# - Provide a tiny initializer that ensures a messages table + index.
# ======================================================================

def resolve_db_path(base_dir: Path, value: Optional[str], default_name: str) -> Path:
    """
    Resolve a user-supplied DB filename or path. If None, use default_name.
    If relative, place it under base_dir. Ensure a .db suffix.
    """
    name = (value or default_name).strip()
    if not name.endswith(".db"):
        name += ".db"
    p = Path(name)
    return p if p.is_absolute() else base_dir / p


# Parse DB-related flags first, leave the rest for the main parser.
db_parser = argparse.ArgumentParser(add_help=False)
db_parser.add_argument("--send_db", required=False, help="Path to the send database file (e.g., --send_db outbox.db). Relative paths are placed next to this Python file.")
db_parser.add_argument("--recv_db", required=False, help="Path to the receive database file (e.g., --recv_db inbox.db). Relative paths are placed next to this Python file.")
db_args, remaining_argv = db_parser.parse_known_args()

script_dir = Path(os.path.dirname(os.path.abspath(__file__)))

# If only one path is provided, use the same file for both send and receive.
send_name = db_args.send_db or db_args.recv_db or "test.db"
recv_name = db_args.recv_db or db_args.send_db or "test.db"

send_db_path: Path = resolve_db_path(script_dir, send_name, "test.db")
receive_db_path: Path = resolve_db_path(script_dir, recv_name, "test.db")

# Create db_sdk Database instances (one per file).
send_db: Database = Database(send_db_path)
receive_db: Database = Database(receive_db_path)

# Tunables
BATCH_INTERVAL_SECONDS: float = 5.0
SEND_POLL_INTERVAL_SECONDS: float = 1.0


async def initialize_db(db: Database) -> None:
    """
    Create the messages table and a small index using db_sdk.
    """
    await Message.create_table(db)
    await Message.create_index(db, name="idx_messages_state_id", columns=["state", "id"], unique=False)


# ======================================================================
# SECTION 2: SUMMONER LOGIC — HELPERS
# ----------------------------------------------------------------------
# ReceiveBatcher:
#   - Buffers incoming strings and periodically flushes them into the
#     receive DB as rows with state='new'.
#
# SendPoller:
#   - Reads rows with state='new' from the send DB in FIFO order,
#     marks them 'processed', and returns the data list.
# ======================================================================

class ReceiveBatcher:
    """
    Buffer incoming messages and periodically flush them into the receive DB.
    Messages are inserted with state='new' using db_sdk.
    """

    def __init__(self, db: Database, batch_interval: float = BATCH_INTERVAL_SECONDS) -> None:
        self.db: Database = db
        self.batch_interval: float = batch_interval
        self._buffer: list[str] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._timer_task: Optional[asyncio.Task] = None

    async def queue(self, content: str) -> None:
        """
        Append a new message to the in-memory buffer.
        A flush task is scheduled when the buffer transitions from empty to non-empty.
        """
        async with self._lock:
            self._buffer.append(content)
            if self._timer_task is None:
                self._timer_task = asyncio.create_task(self._run_flush_cycle())

    async def _run_flush_cycle(self) -> None:
        """
        Sleep for batch_interval, then copy and clear the buffer under lock.
        Insert each message via db_sdk and print after commit.
        """
        await asyncio.sleep(self.batch_interval)

        async with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
            self._timer_task = None

        if not batch:
            return

        # Insert via db_sdk. One call per row keeps the abstraction simple.
        for msg in batch:
            await Message.insert(self.db, data=msg, state="new")

        # Print after inserts are committed by db_sdk (no JSON parsing here).
        for msg in batch:
            tag = "\r[From server]" if isinstance(msg, str) and msg.startswith("Warning:") else "\r[Received]"
            await aprint(tag, str(msg))


class SendPoller:
    """
    Poll the send database for new messages using db_sdk.
    - Reads rows with state='new' in FIFO order.
    - Marks them processed using db_sdk.
    - Returns the data as a list of strings.
    """

    def __init__(self, db: Database, poll_interval: float = SEND_POLL_INTERVAL_SECONDS) -> None:
        self.db: Database = db
        self.poll_interval: float = poll_interval
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get_new_messages(self) -> list[str]:
        while True:
            async with self._lock:
                rows = await Message.find(
                    self.db,
                    where={"state": "new"},
                    fields=["id", "data"],
                    order_by="id",
                )

                if rows:
                    # Mark processed using db_sdk. One update per row keeps the interface simple.
                    for row in rows:
                        await Message.update(self.db, where={"id": row["id"]}, fields={"state": "processed"})
                    return [row["data"] for row in rows]

            await asyncio.sleep(self.poll_interval)


# ======================================================================
# SECTION 3: SUMMONER CLIENT, ROUTES & APP STARTUP
# ----------------------------------------------------------------------
# - Instantiate the Summoner client.
# - Initialize DBs and helper objects in setup().
# - Implement the @receive and @send routes.
# - Parse remaining CLI flags and run the client.
# ======================================================================

client = SummonerClient(name="ConnectAgent_0")

batcher: Optional[ReceiveBatcher] = None
poller: Optional[SendPoller] = None


async def setup() -> None:
    """
    Initialize both databases and construct helper objects.
    """
    global batcher, poller
    await initialize_db(receive_db)
    await initialize_db(send_db)
    batcher = ReceiveBatcher(receive_db)
    poller = SendPoller(send_db)


@client.receive(route="")
async def custom_receive(msg: Any) -> None:
    """
    Summoner receive handler.
    Persist the full incoming message as JSON (true relay).
    We try to serialize anything received; if it can't be serialized, we ignore it.
    """
    global batcher
    if batcher is None:
        return
    try:
        # Queue stores the JSON string in-memory; a timed flush later writes to the DB.
        await batcher.queue(json.dumps(msg).strip())
    except Exception:
        pass


@client.send(route="", multi=True)
async def custom_send() -> list:
    """
    Summoner send handler.
    multi=True lets the client emit a list of messages as separate sends (async-friendly).
    We fetch pending rows, parse JSON when possible, otherwise return the raw string.
    """
    global poller
    if poller is None:
        return []
    msg_str_list = await poller.get_new_messages()
    out = []
    for s in msg_str_list:
        try:
            out.append(json.loads(s))   # structured message
        except Exception:
            out.append(s)               # fallback: raw string preserved
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.loop.run_until_complete(setup())

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        asyncio.run(send_db.close())
        asyncio.run(receive_db.close())
