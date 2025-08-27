#!/usr/bin/env python3
# mcp_sim.py — interactive shim for ConnectAgent_1 (raw aiosqlite)

import argparse
import asyncio
from pathlib import Path
from typing import Optional

import aiosqlite
import os

def resolve_db_path(base_dir: Path, value: Optional[str], default_name: str) -> Path:
    name = (value or default_name).strip()
    if not name.endswith(".db"):
        name += ".db"
    p = Path(name)
    return p if p.is_absolute() else base_dir / p


# DB flags (same style as the agents)
db_parser = argparse.ArgumentParser(add_help=False)
db_parser.add_argument("--send_db", required=False, help="Path to the send database file (e.g., --send_db outbox.db). Relative paths are placed next to this Python file.")
db_parser.add_argument("--recv_db", required=False, help="Path to the receive database file (e.g., --recv_db inbox.db). Relative paths are placed next to this Python file.")
db_args, remaining_argv = db_parser.parse_known_args()

script_dir = Path(__file__).resolve().parent
send_name = db_args.send_db or db_args.recv_db or "test.db"
recv_name = db_args.recv_db or db_args.send_db or "test.db"

send_db_path: Path    = resolve_db_path(script_dir, send_name, "test.db")
receive_db_path: Path = resolve_db_path(script_dir, recv_name, "test.db")


async def initialize_db(path: Path) -> None:
    """
    Ensure the messages table exists. Use short-lived connection and then close it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path)) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS messages (data TEXT NOT NULL, state TEXT NOT NULL)"
        )
        # Keep schema minimal and compatible with ConnectAgent_1.
        await db.commit()


async def cmd_get() -> None:
    """
    Read all 'new' rows from send_db (FIFO by rowid), print them, then mark them processed.
    Uses short-lived connections like ConnectAgent_1.
    """
    # Read step
    async with aiosqlite.connect(str(send_db_path)) as db:
        cur = await db.execute(
            "SELECT rowid, data FROM messages WHERE state = 'new' ORDER BY rowid"
        )
        rows = await cur.fetchall()
        await cur.close()

    if not rows:
        print("(no new messages)")
        return

    for _rid, data in rows:
        print(data)

    # Update step (short-lived connection)
    rowids = [r[0] for r in rows]
    placeholders = ",".join("?" for _ in rowids)
    update_sql = f"UPDATE messages SET state = 'processed' WHERE rowid IN ({placeholders})"
    async with aiosqlite.connect(str(send_db_path)) as db:
        await db.execute(update_sql, rowids)
        await db.commit()


async def cmd_post(message: str) -> None:
    """
    Insert a 'new' message into recv_db exactly as provided.
    """
    async with aiosqlite.connect(str(receive_db_path)) as db:
        await db.execute(
            "INSERT INTO messages (data, state) VALUES (?, 'new')",
            (message,),
        )
        await db.commit()
    print("ok")


async def ainput(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def repl() -> None:
    # Prepare DBs (create tables if missing)
    await initialize_db(send_db_path)
    await initialize_db(receive_db_path)

    print(f"send_db: {send_db_path}")
    print(f"recv_db: {receive_db_path}")
    print("Type /help for commands.")

    try:
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
    finally:
        # nothing to close because we use short-lived connections everywhere
        pass


if __name__ == "__main__":
    asyncio.run(repl())
