import random
import uuid
from datetime import datetime
from pathlib import Path
from db_sdk import Model, Field
from typing import Optional

# ─────── Module-level DB path ───────
DB_PATH: Optional[Path] = None

def configure_db_path(path: Path):
    """
    Configure the SQLite file path for this agent.
    Call this before any of the async functions below.
    """
    global DB_PATH
    DB_PATH = path

def _db() -> Path:
    assert DB_PATH is not None, "You must call configure_db_path() first"
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH

# ─────── Model Definitions ───────

class State(Model):
    __tablename__ = "state"
    agent_id               : str   = Field("TEXT", primary_key=True)
    transaction_id         : str   = Field("TEXT", default=None)
    current_offer          : float = Field("REAL", default=0.0)
    agreement              : str   = Field("TEXT", default="none")
    negotiation_active     : int   = Field("INTEGER", default=0, check="negotiation_active IN (0,1)")
    limit_acceptable_price : float = Field("REAL", default=0.0)
    price_shift            : float = Field("REAL", default=0.0)

class History(Model):
    __tablename__ = "history"
    id        : int    = Field("INTEGER", primary_key=True)
    agent_id  : str    = Field("TEXT")
    success   : int    = Field("INTEGER", default=0, check="success IN (0,1)")
    txid      : str    = Field("TEXT")
    timestamp : str    = Field("TEXT")

# ─────── Initialization ───────

async def init_db():
    """
    Create 'state' and 'history' tables (if not exists)
    and add a unique index on (agent_id, txid).
    """
    db = _db()
    await State.create_table(db)
    await History.create_table(db)
    # enforce unique (agent_id, txid)
    await History.create_index(
        db,
        name="idx_history_agent_tx",
        columns=["agent_id", "txid"],
        unique=True
    )

# ─────── State Helpers ───────

async def create_or_reset_state(agent_id: str):
    """
    Ensure a 'state' row exists for this agent; do not overwrite if present.
    """
    db = _db()
    await State.insert_or_ignore(db, agent_id=agent_id)

async def set_state_fields(agent_id: str, **fields):
    """
    Update arbitrary fields for a given agent_id in 'state'.
    """
    if not fields:
        return
    db = _db()
    await State.update(db, where={"agent_id": agent_id}, fields=fields)

async def get_state(agent_id: str) -> Optional[dict]:
    """
    Return the state row as a dict, or None if missing.
    """
    db = _db()
    rows = await State.filter(
        db,
        filter={"agent_id": agent_id},
        fields=["transaction_id","current_offer","agreement",
                "negotiation_active","limit_acceptable_price","price_shift"]
    )
    return rows[0] if rows else None

async def get_active_agents() -> list[str]:
    """
    Return list of agent_ids with an active negotiation.
    """
    db = _db()
    rows = await State.filter(db, filter={"negotiation_active": 1}, fields=["agent_id"])
    return [r["agent_id"] for r in rows]

# ─────── History Helpers ───────

async def add_history(agent_id: str, success: int, txid: str) -> bool:
    """
    Insert one record into 'history' unless (agent_id,txid) already exists.
    Returns True if inserted, False if skipped.
    """
    db = _db()
    timestamp = datetime.now().isoformat()
    rid = await History.insert_or_ignore(
        db,
        agent_id=agent_id,
        success=success,
        txid=txid,
        timestamp=timestamp
    )
    return rid is not None

async def get_history(agent_id: str) -> list[dict]:
    """
    Return list of {'success':…, 'txid':…} for this agent, ordered by insertion.
    """
    db = _db()
    return await History.filter(
        db,
        filter={"agent_id": agent_id},
        fields=["success","txid"],
        order_by="id"
    )

async def show_statistics(agent_id: str) -> dict:
    """
    Compute success rate and last txid for agent.
    """
    history = await get_history(agent_id)
    total = len(history)
    successes = sum(row["success"] for row in history)
    last_txid = history[-1]["txid"] if history else None
    rate = (successes / total * 100) if total else 0
    return {
        "agent_id": agent_id,
        "rate": rate,
        "successes": successes,
        "total": total,
        "last_txid": last_txid
    }

# ─────── Negotiation Starters ───────

async def start_negotiation_seller(agent_id: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    await create_or_reset_state(agent_id)
    min_p = random.randint(60, 90)
    dec   = random.randint(1, 5)
    curr  = random.randint(min_p, 100)
    txid  = str(uuid.uuid4())
    await set_state_fields(
        agent_id,
        limit_acceptable_price=min_p,
        price_shift=dec,
        current_offer=curr,
        agreement="none",
        negotiation_active=1,
        transaction_id=txid,
    )
    return txid

async def start_negotiation_buyer(agent_id: str, txid: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    max_p = random.randint(65, 80)
    inc   = random.randint(1, 5)
    curr  = random.randint(1, max_p)
    await set_state_fields(
        agent_id,
        limit_acceptable_price=max_p,
        price_shift=inc,
        current_offer=curr,
        agreement="none",
        negotiation_active=1,
        transaction_id=txid
    )
    return txid
