import argparse
import asyncio
import os
from pathlib import Path
from typing import Optional

from db_sdk import Database
from db_models import Message


def resolve_db_path(base_dir: Path, value: Optional[str], default_name: str) -> Path:
    name = (value or default_name).strip()
    if not name.endswith(".db"):
        name += ".db"
    p = Path(name)
    return p if p.is_absolute() else base_dir / p


# DB flags first (same style as the agent)
db_parser = argparse.ArgumentParser(add_help=False)
db_parser.add_argument("--send_db", required=False, help="Path to the send database file (e.g., --send_db outbox.db). Relative paths are placed next to this Python file.")
db_parser.add_argument("--recv_db", required=False, help="Path to the receive database file (e.g., --recv_db inbox.db). Relative paths are placed next to this Python file.")
db_args, remaining_argv = db_parser.parse_known_args()

script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
send_name = db_args.send_db or db_args.recv_db or "test.db"
recv_name = db_args.recv_db or db_args.send_db or "test.db"

send_db_path: Path = resolve_db_path(script_dir, send_name, "test.db")
receive_db_path: Path = resolve_db_path(script_dir, recv_name, "test.db")

send_db: Database = Database(send_db_path)
receive_db: Database = Database(receive_db_path)


async def initialize_db(db: Database) -> None:
    await Message.create_table(db)
    await Message.create_index(db, name="idx_messages_state_id", columns=["state", "id"], unique=False)


async def cmd_get() -> None:
    """
    Print all 'new' messages from send_db and mark them processed.
    """
    await initialize_db(send_db)
    rows = await Message.find(send_db, where={"state": "new"}, fields=["id", "data"], order_by="id")
    if not rows:
        print("(no new messages)")
        return

    for row in rows:
        print(row["data"])

    for row in rows:
        await Message.update(send_db, where={"id": row["id"]}, fields={"state": "processed"})


async def cmd_post(message: str) -> None:
    """
    Insert a 'new' message into recv_db. Stored exactly as provided.
    """
    await initialize_db(receive_db)
    await Message.insert(receive_db, data=message, state="new")
    print("ok")


async def ainput(prompt: str) -> str:
    """
    Async wrapper around input() so we stay on one event loop.
    """
    return await asyncio.to_thread(input, prompt)


async def repl() -> None:
    """
    Simple interactive loop:
      /get
      /post <text>
      /post            (then prompted)
      /help
      /exit | /quit | q
    """
    print(f"send_db: {send_db_path}")
    print(f"recv_db: {receive_db_path}")
    print("Type /help for commands.")
    while True:
        try:
            line = (await ainput("mcp> ")).strip()
        except EOFError:
            break
        if not line:
            continue

        if line in ("/exit", "exit", "/quit", "quit", "q"):
            break

        if line in ("/help", "help", "?"):
            print("Commands:")
            print("  /get                 - print and consume new messages from send_db")
            print("  /post <text>         - insert <text> as a new message into recv_db")
            print("  /post                - prompt for one line to post")
            print("  /exit                - quit")
            continue

        if line.startswith("/get"):
            await cmd_get()
            continue

        if line.startswith("/post"):
            payload = line[len("/post"):].strip()
            if not payload:
                payload = (await ainput("message> ")).rstrip("\n")
            if payload:
                await cmd_post(payload)
            else:
                print("(empty message ignored)")
            continue

        print("Unknown command. Type /help.")

    await send_db.close()
    await receive_db.close()


if __name__ == "__main__":
    asyncio.run(repl())
