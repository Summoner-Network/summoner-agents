import argparse
import asyncio
import os, json
from pathlib import Path
from typing import Any, List, Optional
from aioconsole import aprint

from summoner.client import SummonerClient
import aiosqlite


# ======================================================================
# SECTION 1: DATABASE (raw SQLite)
# ----------------------------------------------------------------------
# - Parses optional CLI flags for send/receive DB paths.
# - Resolves paths (relative paths are placed next to this file).
# - Ensures a minimal schema: messages(data TEXT, state TEXT).
# - Provides tunables for batching and polling intervals.
# ======================================================================

def resolve_db_path(base_dir: Path, value: Optional[str], default_name: str) -> Path:
    """
    Return an absolute path for a SQLite database file.
    If `value` is None, use `default_name`.
    If the provided value lacks a .db suffix, add it.
    """
    name = (value or default_name).strip()
    if not name.endswith(".db"):
        name += ".db"
    p = Path(name)
    return p if p.is_absolute() else base_dir / p


# Parse DB-related flags first; other flags are parsed later.
db_parser = argparse.ArgumentParser(add_help=False)
db_parser.add_argument("--send_db", required=False, help="Path to the send database file (e.g., --send_db outbox.db). Relative paths are placed next to this Python file.")
db_parser.add_argument("--recv_db", required=False, help="Path to the receive database file (e.g., --recv_db inbox.db). Relative paths are placed next to this Python file.")
db_args, remaining_argv = db_parser.parse_known_args()

script_dir = Path(os.path.dirname(os.path.abspath(__file__)))

# If only one path is provided, the same file is used for both send and receive.
send_name = db_args.send_db or db_args.recv_db or "test.db"
recv_name = db_args.recv_db or db_args.send_db or "test.db"

send_db_path: Path    = resolve_db_path(script_dir, send_name, "test.db")
receive_db_path: Path = resolve_db_path(script_dir, recv_name, "test.db")

BATCH_INTERVAL_SECONDS: float = 5.0
SEND_POLL_INTERVAL_SECONDS: float = 1.0


async def initialize_db(path: Path) -> None:
    """
    Ensure the SQLite database exists and contains the `messages` table.
    The table stores message payloads (`data`) and a processing state (`state`).
    """
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS messages (data TEXT NOT NULL, state TEXT NOT NULL)"
        )
        await db.commit()


# ======================================================================
# SECTION 2: SUMMONER LOGIC — HELPERS
# ----------------------------------------------------------------------
# ReceiveBatcher:
#   - Buffers incoming strings in memory.
#   - Periodically writes the buffered strings to the receive DB as rows
#     with state='new'.
#   - Prints flushed messages to the terminal after the commit.
#
# SendPoller:
#   - Retrieves rows with state='new' from the send DB in FIFO order
#     using rowid.
#   - Marks retrieved rows as state='processed'.
#   - Returns the collected payloads as a list of strings.
# ======================================================================

class ReceiveBatcher:
    """
    Buffers incoming messages and flushes them to the database at fixed intervals.
    Each flushed message is inserted with state='new'.
    """

    def __init__(self, db_path: Path, batch_interval: float = BATCH_INTERVAL_SECONDS):
        self.db_path = db_path
        self.batch_interval = batch_interval
        self._buffer: List[str] = []
        self._lock = asyncio.Lock()
        self._timer_task: Optional[asyncio.Task] = None

    async def queue(self, content: str) -> None:
        """
        Append a message to the in-memory buffer.
        Starts a background flush task on the first appended message.
        """
        async with self._lock:
            self._buffer.append(content)
            if self._timer_task is None:
                self._timer_task = asyncio.create_task(self._run_flush_cycle())

    async def _run_flush_cycle(self) -> None:
        """
        Sleep for the batch interval, then:
          - Copy and clear the buffer under a lock
          - Insert the copied messages as state='new'
          - Print each inserted message
        """
        await asyncio.sleep(self.batch_interval)

        async with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
            self._timer_task = None

        if not batch:
            return

        # Short-lived connection for the insert batch.
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.executemany(
                "INSERT INTO messages (data, state) VALUES (?, 'new')",
                [(msg,) for msg in batch],
            )
            await db.commit()

        # Print after commit to reflect what was persisted.
        for msg in batch:
            tag = "\r[From server]" if isinstance(msg, str) and msg.startswith("Warning:") else "\r[Received]"
            await aprint(tag, str(msg))


class SendPoller:
    """
    Polls the send database for new messages and acknowledges them.
    The retrieval is FIFO based on rowid.
    """

    def __init__(self, db_path: Path, poll_interval: float = SEND_POLL_INTERVAL_SECONDS):
        self.db_path = db_path
        self.poll_interval = poll_interval

    async def get_new_messages(self) -> List[str]:
        """
        Return a list of message payloads when available.
        If none are available, wait for `poll_interval` seconds and retry.
        """
        while True:
            # Read step (short-lived connection).
            async with aiosqlite.connect(str(self.db_path)) as db:
                cursor = await db.execute(
                    "SELECT rowid, data FROM messages WHERE state = 'new' ORDER BY rowid"
                )
                rows = await cursor.fetchall()
                await cursor.close()

            if rows:
                rowids = [row[0] for row in rows]
                messages = [row[1] for row in rows]
                placeholders = ",".join("?" for _ in rowids)
                update_sql = f"UPDATE messages SET state = 'processed' WHERE rowid IN ({placeholders})"

                # Acknowledge step (short-lived connection).
                async with aiosqlite.connect(str(self.db_path)) as db:
                    await db.execute(update_sql, rowids)
                    await db.commit()

                return messages

            await asyncio.sleep(self.poll_interval)


# ======================================================================
# SECTION 3: SUMMONER CLIENT, HANDLERS & APP STARTUP
# ----------------------------------------------------------------------
# - Instantiates the Summoner client.
# - Initializes both databases and the helper objects.
# - Receive handler:
#     * Serializes the entire incoming message as JSON text and queues it
#       for batched insertion into the receive database.
# - Send handler:
#     * Reads new messages from the send database and returns them.
#     * Attempts to parse each row as JSON; if parsing fails, returns
#       the raw string.
# - Starts the app with CLI-configurable config path.
# ======================================================================

client = SummonerClient(name="ConnectAgent_1")

batcher: Optional[ReceiveBatcher] = None
poller:  Optional[SendPoller]    = None


async def setup():
    """
    Create the `messages` table in both databases and instantiate helpers.
    """
    global batcher, poller
    await initialize_db(receive_db_path)
    await initialize_db(send_db_path)
    batcher = ReceiveBatcher(receive_db_path)
    poller  = SendPoller(send_db_path)


@client.receive(route="")
async def custom_receive(msg: Any) -> None:
    """
    Receive handler.
    Stores the full incoming object as JSON text in the receive database.
    Non-serializable inputs are ignored.
    """
    global batcher
    if batcher is None:
        return
    try:
        await batcher.queue(json.dumps(msg).strip())
    except Exception:
        pass


@client.send(route="", multi=True)
async def custom_send() -> list:
    """
    Send handler.
    Returns any pending rows from the send database.
    Each row is parsed from JSON when possible; otherwise, the raw string is returned.
    """
    global poller
    if poller is None:
        return []
    msg_str_list = await poller.get_new_messages()
    out = []
    for s in msg_str_list:
        try:
            out.append(json.loads(s))
        except Exception:
            out.append(s)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.loop.run_until_complete(setup())

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")