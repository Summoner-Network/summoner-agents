import random
import uuid
from datetime import datetime
from typing import Optional, Dict, List

from db_sdk import Model, Field, Database

# ─────────────── Handshake ───────────────

class RoleState(Model):
    """
    Per-peer, per-role state row.
    """
    __tablename__ = "role_state"
    id                   = Field("INTEGER", primary_key=True)
    self_id              = Field("TEXT", nullable=False)             # this agent
    role                 = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id              = Field("TEXT", nullable=False)             # the other agent
    state                = Field("TEXT", nullable=True)              # init_*, resp_*
    local_nonce          = Field("TEXT", nullable=True)
    peer_nonce           = Field("TEXT", nullable=True)
    local_reference      = Field("TEXT", nullable=True)
    peer_reference       = Field("TEXT", nullable=True)
    exchange_count       = Field("INTEGER", default=0, nullable=False)
    finalize_retry_count = Field("INTEGER", default=0, nullable=False)
    peer_address         = Field("TEXT", nullable=True)
    created_at           = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)
    updated_at           = Field("DATETIME", on_update=True, nullable=False)

class NonceEvent(Model):
    """
    Append-only nonce log for the *current* conversation with a given peer.
    Clear rows by (self_id, role, peer_id) after final handshake.
    """
    __tablename__ = "nonce_event"
    id         = Field("INTEGER", primary_key=True)
    self_id    = Field("TEXT", nullable=False)
    role       = Field("TEXT", nullable=False, check="role IN ('initiator','responder')")
    peer_id    = Field("TEXT", nullable=False)
    flow       = Field("TEXT", nullable=False, check="flow IN ('sent','received')")
    nonce      = Field("TEXT", nullable=False)
    created_at = Field("DATETIME", default="CURRENT_TIMESTAMP", nullable=False)

# ─────────────── Negotiation ───────────────

class TradeState(Model):
    __tablename__ = "trade_state"
    agent_id                = Field("TEXT", primary_key=True)
    transaction_id          = Field("TEXT", default=None)
    current_offer           = Field("REAL", default=0.0)
    agreement               = Field("TEXT", default=None)
    limit_acceptable_price  = Field("REAL", default=0.0)
    price_shift             = Field("REAL", default=0.0)

class History(Model):
    __tablename__ = "history"
    # AUTOINCREMENT parity with pre-ORM version:
    id         = Field("INTEGER PRIMARY KEY AUTOINCREMENT")  # do not set primary_key=True here
    agent_id   = Field("TEXT")
    success    = Field("INTEGER", default=0, check="success IN (0,1)")
    txid       = Field("TEXT")
    timestamp  = Field("TEXT")


# ─────────────── TradeState helpers ───────────────

async def create_or_reset_state(db: Database, agent_id: str) -> None:
    """
    Ensure a 'state' row exists for this agent; do not overwrite if present.
    """
    await TradeState.insert_or_ignore(db, agent_id=agent_id)

async def set_state_fields(db: Database, agent_id: str, **fields) -> None:
    """
    Update arbitrary fields for a given agent_id in 'state'.
    """
    if not fields:
        return
    await TradeState.update(db, where={"agent_id": agent_id}, fields=fields)

async def get_state(db: Database, agent_id: str) -> Optional[Dict]:
    """
    Return the state row as a dict, or None if missing.
    """
    rows = await TradeState.find(
        db,
        where={"agent_id": agent_id},
        fields=[
            "transaction_id", "current_offer", "agreement", "limit_acceptable_price", "price_shift"
        ]
    )
    return rows[0] if rows else None

# ─────────────── History helpers ───────────────

async def add_history(db: Database, agent_id: str, success: int, txid: str) -> bool:
    """
    Insert one record into 'history' unless (agent_id, txid) already exists.
    Returns True if inserted, False if skipped.
    """
    ts = datetime.now().isoformat()
    cur = await db.execute(
        """
        INSERT INTO history (agent_id, success, txid, timestamp)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(agent_id, txid) DO NOTHING
        """,
        (agent_id, success, txid, ts),
    )
    await db.commit()
    # rowcount == 1 → inserted; 0 → ignored as duplicate
    return getattr(cur, "rowcount", 0) == 1

async def get_history(db: Database, agent_id: str) -> List[Dict]:
    """
    Return list of {'success': ..., 'txid': ...} for this agent, ordered by insertion id.
    """
    return await History.find(
        db,
        where={"agent_id": agent_id},
        fields=["success", "txid"],
        order_by="id"
    )

async def show_statistics(db: Database, agent_id: str) -> Dict:
    """
    Compute success rate and last txid for agent.
    """
    history = await get_history(db, agent_id)
    total = len(history)
    successes = sum(row["success"] for row in history)
    last_txid = history[-1]["txid"] if history else None
    rate = (successes / total * 100) if total else 0
    return {
        "agent_id": agent_id,
        "rate": rate,
        "successes": successes,
        "total": total,
        "last_txid": last_txid,
    }


# ─────────────── Negotiation starters ───────────────

async def start_negotiation_seller(db: Database, agent_id: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    await create_or_reset_state(db, agent_id)
    min_p = random.randint(60, 90)
    dec   = random.randint(1, 5)
    curr  = random.randint(min_p, 100)
    txid  = str(uuid.uuid4())
    await set_state_fields(
        db, agent_id,
        limit_acceptable_price=min_p,
        price_shift=dec,
        current_offer=curr,
        agreement=None,
        transaction_id=txid,
    )
    return txid

async def start_negotiation_buyer(db: Database, agent_id: str, txid: str) -> str:
    """
    Initialize a fresh negotiation for agent_id, randomize terms, return txid.
    """
    max_p = random.randint(65, 80)
    inc   = random.randint(1, 5)
    curr  = random.randint(1, max_p)
    await set_state_fields(
        db, agent_id,
        limit_acceptable_price=max_p,
        price_shift=inc,
        current_offer=curr,
        agreement=None,
        transaction_id=txid,
    )
    return txid

