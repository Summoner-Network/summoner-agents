# =============================================================================
# HSAgent_0 — Handshake + Nonce Exchange Demo
#
# OVERVIEW
#   Two roles:
#     - Responder  : resp_ready → resp_confirm  → resp_exchange         → resp_finalize        → resp_ready
#
#   High-level:
#     1) Discovery / Hello            : register ↔ confirm
#     2) Nonce Ping-Pong              : request/respond (bounded by EXCHANGE_LIMIT on the other seller side)
#     3) Finalize (swap references)   : conclude/finish, then init sends close until retries count is exceeded
#     4) Reconnect                    : initiator can rejoin if both sides remember refs
#
# KEY IDEAS
#   - RoleState is per (self_id, role, peer_id). It stores live state, local/peer nonces,
#     local/peer references, retry counters, and last known peer address.
#   - NonceEvent is a per-peer log of sent/received nonces during an active exchange.
#     It is cleared when both sides confirm final references.
#   - The send driver runs periodically. It emits outbound messages based on RoleState.
#   - The upload/download pair negotiates which states are permissible per role+peer.
#
# INVARIANTS
#   1) Every respond/request must echo the last counterpart nonce as `your_nonce`.
#   2) finalize requires both sides to present refs consistently:
#        - Initiator sends conclude(my_ref).
#        - Responder sends finish(your_ref=peer_reference, my_ref=local_reference).
#        - Initiator sends close(your_ref=peer_reference, my_ref=local_reference) until retries count is exceeded.
#   3) On reconnect, initiator must present the responder's last local_reference as `your_ref`.
#
# TUNABLES
#   - RESP_FINAL_LIMIT   : number of finalize retries before cutting back to ready.
# =============================================================================


""" ============================ IMPORTS & TYPES ============================ """
from summoner.client import SummonerClient
from summoner.protocol import Move, Stay, Node, Direction, Event
import argparse
import asyncio
import uuid
import random
from typing import Any, Optional, Callable
from pathlib import Path



""" ======================== CONSTANTS & SIMPLE HELPERS ===================== """
# Counters to simulate conversation with several exchanges.
# exchange = alternating request/response rounds before we cut to finalize
# finalize = # of "finish/close" attempts before cutting back to ready

RESP_FINAL_LIMIT = 5 # Needs to wait for "conclude"

def generate_random_digits():
    # Nonces/refs are short tokens used for demonstration purposes.
    return ''.join(random.choices('123456789', k=10))

# my agent ID (used in client name and to partition rows in the DB)
my_id = str(uuid.uuid4())



""" =========================== DATABASE WIRING ============================= """

from db_sdk import Database
from db_models import RoleState, NonceEvent

# Negotiation models/helpers reused from the old agents
from db_models import (
    TradeState, History,
    create_or_reset_state, get_state,
    set_state_fields, add_history, show_statistics, start_negotiation_buyer,
)

# Each agent instance uses its own on-disk DB. This partitions rows per agent run.
db_path = Path(__file__).resolve().parent / f"HSBuyAgent-{my_id}.db"
db = Database(db_path)

async def setup() -> None:
    """
    Create tables and the indexes we rely on for uniqueness and scanning.

    Index strategy:
       - Uniqueness per conversation thread: (self_id, role, peer_id)
       - Fast scans for the send loop: (self_id, role)
       - Filtering and cleanup for nonce logs: (self_id, role, peer_id)

    DATA MODEL SUMMARY
      RoleState:
        - state: one of the Node names for the given role
        - local_nonce / peer_nonce: ping-pong tokens during exchange
        - local_reference / peer_reference: opaque refs exchanged at finalize
        - exchange_count: counts request/respond rounds per thread
        - finalize_retry_count: counts finalize attempts before cut
        - peer_address: last known transport address for convenience
      NonceEvent:
        - flow ∈ {sent, received}, nonce value, and who it's associated with
    """
    await RoleState.create_table(db)
    await NonceEvent.create_table(db)

    await RoleState.create_index(db, "uq_role_peer", ["self_id", "role", "peer_id"], unique=True)
    await RoleState.create_index(db, "ix_role_scan", ["self_id", "role"], unique=False)
    await NonceEvent.create_index(db, "ix_nonce_triplet", ["self_id", "role", "peer_id"], unique=False)

    # Negotiation
    await TradeState.create_table(db)
    await History.create_table(db)

    await TradeState.create_index(db, "ix_state_agent", ["agent_id"], unique=False)
    await History.create_index(db, "ix_history_agent", ["agent_id"], unique=False)
    await History.create_index(db, name="idx_history_agent_tx", columns=["agent_id", "txid"], unique=True)



""" ========================= CLIENT & FLOW SETUP =========================== """

client = SummonerClient(name=f"HSBuyAgent_0")

# We activate a flow diagram to orchestrate the client's routes
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")
client_flow.ready()

# Trigger tokens (e.g., ok, ignore, error) used to drive Move/Stay decisions.
Trigger = client_flow.triggers()

# ==== Handshake phases (renamed for clarity) ====
# Responder: resp_ready -> resp_confirm  -> resp_exchange         -> resp_finalize        -> resp_ready
#
# *_ready         : idle/buffer state
# *_exchange      : alternating nonce ping-pong
# resp_finalize   : responder returns its reference (finish)



""" ======================= ROLESTATE HELPERS (UTILS) ======================= """

async def ensure_role_state(self_id: str, role: str, peer_id: str, default_state: str) -> dict:
    """
    Ensure a RoleState row exists for (self_id, role, peer_id). If present with NULL state,
    normalize to default_state. Return the row as a dict so callers can read fields.

    Why this exists:
      - Receive handlers often need to validate a peer's message against the last
        known local value (nonce/ref). Creating/normalizing here avoids None surprises.
    """
    rows = await RoleState.find(db, where={"self_id": self_id, "role": role, "peer_id": peer_id})
    if rows:
        row = rows[0]
        if not row.get("state"):
            await RoleState.update(db, where={"self_id": self_id, "role": role, "peer_id": peer_id}, fields={"state": default_state})
            row["state"] = default_state
        return row
    await RoleState.insert(db, self_id=self_id, role=role, peer_id=peer_id, state=default_state)
    return {
        "self_id": self_id, 
        "role": role, 
        "peer_id": peer_id, 
        "state": default_state,
        "local_nonce": None, 
        "peer_nonce": None, 
        "local_reference": None, 
        "peer_reference": None,
        "exchange_count": 0, 
        "finalize_retry_count": 0, 
        "peer_address": None
    }

async def start_trade_with(peer_id, txid):
    await create_or_reset_state(db, peer_id)
    peer_row = await get_state(db, peer_id)
    if peer_row["transaction_id"] is None:
        await start_negotiation_buyer(db, peer_id, txid)
        peer_row = await get_state(db, peer_id)
        client.logger.info(
                f"\033[94m[{client.name}|{my_id[:5]}] Started with {peer_id[:10]}. "
                f"OFFER={peer_row['current_offer']}, "
                f"MAX={peer_row['limit_acceptable_price']}, TXID={txid}\033[0m"
                )

async def end_trade_with(peer_id):
    stats = await show_statistics(db, peer_id)

    agent_id = stats["agent_id"]
    rate = stats["rate"]
    successes = stats["successes"]
    total = stats["total"]
    last_txid = stats["last_txid"]
    client.logger.info(
        f"\033[95m[{client.name}] Agent {agent_id} — Success rate: "
        f"{rate:.2f}% ({successes}/{total}), Last TXID: {last_txid}\033[0m"
    )
    await set_state_fields(db, 
        peer_id,
        agreement=None,
        transaction_id=None
    )

def decoration_generator(route: str):
    def hs_decorator(fn: Callable[[dict], Optional[Event]]):
        async def decorated_fn(payload: dict) -> Optional[Event]:
            
            # Handshake logic
            addr = payload["remote_addr"]
            content = payload["content"]
            peer_id = content["from"]

            if not(content["intent"] == "request" and content["to"] is not None): return Stay(Trigger.ignore)
            client.logger.info(f"[{route}] intent OK")

            if not("your_nonce" in content and "my_nonce" in content):
                return Stay(Trigger.ignore)
            client.logger.info(f"[{route}] validation OK")

            row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
            client.logger.info(f"[{route}] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
            if row.get("local_nonce") != content["your_nonce"]:
                return Stay(Trigger.ignore)
            
            rows = await NonceEvent.find(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id})
            nonces = [row["nonce"] for row in rows]
            if content["my_nonce"] in nonces:
                client.logger.info(f"[{route}] received my_nonce={content['my_nonce']!r} previously used")
                return Stay(Trigger.ignore)

            # request: continue ping-pong, bump exchange_count, store their my_nonce, and clear ours
            new_count = int(row.get("exchange_count", 0)) + 1
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "exchange_count": new_count, "peer_address": addr})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
            client.logger.info(f"[{route}] REQUEST RECEIVED #{new_count}")

            return await fn(payload)
        return decorated_fn
    return hs_decorator



""" ============== STATE ADVERTISING (UPLOAD/DOWNLOAD NEGOTIATION) ========= """

@client.upload_states()
async def upload(payload: dict) -> dict[str, str]:
    """
    Called periodically by the client runtime.

    Input:
      payload may include 'from' at the top level or inside payload['content'].

    Behavior:
      - If we don't know the peer (no 'from'), return {} to avoid advertising global keys.
      - Otherwise, respond with peer-scoped keys, e.g.:
          {"responder:<peer>": <state>, "buyer:<peer>": <state>}
      - Each value is the current RoleState.state for that peer or a role-default.
    """
    peer_id = None
    if isinstance(payload, dict):
        peer_id = payload.get("from") or (payload.get("content", {}) or {}).get("from")

    if peer_id is None:
        # No peer: don't advertise global keys; client will retry with a peer.
        return {}

    # Peer-scoped advertisement, e.g. {"initiator:<peer>": "...", "responder:<peer>": "..."}
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields=["state"])

    resp_state = resp_rows[0]["state"] if resp_rows and resp_rows[0]["state"] else "resp_ready"

    # Negotiation view
    peer_row = await get_state(db, peer_id)

    seller_state = peer_row["agreement"] if peer_row and peer_row["agreement"] else "resp_exchange_0"

    # Merge / Fork logic
    if resp_state == "resp_exchange_0":
        state_dict = {f"buyer:{peer_id}": seller_state}
    else:
        state_dict = {f"responder:{peer_id}": resp_state}

    client.logger.info(f"\033[92m[upload] peer={peer_id[:5]} | {state_dict}\033[0m")
    return state_dict

@client.download_states()
async def download(possible_states: dict[Optional[str], list[Node]]) -> None:
    """
    The runtime tells us what states are currently permissible *per role and peer*.
    We pick one (by our preference order) and store it in RoleState.state.

    Contract:
      - Keys are peer-scoped like "initiator:<peer_id>" or "buyer:<peer_id>".
      - We ignore global role-only keys (None or no ':').

    Preference order:
      - Responder:   resp_ready > resp_finalize > resp_confirm > resp_exchange_1 > resp_exchange_0
      - Buyer   :    resp_refuse_too > resp_refuse > resp_accept_too > resp_accept > resp_interested > resp_exchange_0
    """
    ordered_states = {
        "responder": ["resp_ready", "resp_finalize", "resp_confirm", "resp_exchange_1", "resp_exchange_0"],
        "buyer":     ["resp_refuse_too", "resp_refuse", "resp_accept_too", "resp_accept", "resp_interested", "resp_exchange_0"], 
    }

    for key, role_states in possible_states.items():
        if key is None:
            continue
        if ":" not in str(key):
            # Ignore global per-role keys entirely
            client.logger.info(f"[download] skipping non-scoped key '{key}'")
            continue

        role, peer_id = key.split(":", 1)
        if role not in ("responder", "buyer") or not peer_id:
            continue

        client.logger.info(f"[download] possible states '{key}': {role_states}")

        # Choose first allowed state by our preference
        target_state = next((s for s in ordered_states[role] if Node(s) in role_states), None)

        if role == "responder" and target_state:
            await RoleState.update(db, where={"self_id": my_id, "role": role, "peer_id": peer_id}, fields={"state": target_state})

        if role == "buyer":
            if target_state:
                await set_state_fields(db, peer_id, agreement=target_state)

            if Node("resp_exchange_1") in role_states:
                await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"state": "resp_exchange_1"})
                client.logger.info(f"[download] '{role}' changed 'responder' -> 'resp_exchange_1' for {peer_id[:5]}")
            
        client.logger.info(f"[download] '{role}' set state -> '{target_state}' for {peer_id[:5]}")

        


""" =============================== HOOKS =================================== """

@client.hook(direction=Direction.RECEIVE)
async def validation(payload: Any) -> Optional[dict]:
    """
    Receive hook: shape and addressing checks.

    Drops the message unless:
      - payload has 'remote_addr' and 'content'
      - content has 'from', 'to', 'intent'
      - content['to'] is either None (broadcast) or equals my_id
      - content['from'] is not None

    Returns payload to keep processing, or None to drop.
    """
    if isinstance(payload, str) and payload.startswith("Warning:"):
        client.logger.warning(f"[server] {payload}")
    if not("remote_addr" in payload and "content" in payload): return
    content = payload["content"]
    if not("from" in content and "to" in content and "intent" in content): return
    if content["to"] is not None and content["to"] != my_id: return
    if content["from"] is None: return
    client.logger.info(f"receiving...\n\n\033[94m[recv][hook] {payload}\033[0m\n")
    return payload

@client.hook(direction=Direction.SEND)
async def signature(payload: Any) -> Optional[dict]:
    """
    Send hook: tag outbound messages with our agent id as 'from' and log.
    """
    if not isinstance(payload, dict): return
    client.logger.info(f"[send][hook] tagging from={my_id[:5]}")
    payload.update({"from": my_id})
    client.logger.info(f"sending...\n\n\033[91m[send][hook] {payload}\033[0m\n")
    return payload



""" ===================== RECEIVE HANDLERS — RESPONDER ====================== """

@client.receive(route="resp_ready --> resp_confirm")
async def handle_register(payload: dict) -> Optional[Event]:
    """
    HELLO (responder side).
      - Accept a 'register' (fresh hello) if 'to' is None and we don't already have a local_reference.
      - Accept a 'reconnect' if the initiator supplies your_ref == our last local_reference.

    Effects:
      - Ensure RoleState row exists and update peer_address.
      - For reconnect, clear our local_reference so a new finalize can proceed cleanly.
    """
    addr = payload["remote_addr"]
    content = payload["content"]

    if not(content["intent"] in ["register", "reconnect"]): return
    client.logger.info("[resp_ready -> resp_confirm] intent OK")

    peer_id = content["from"]

    # Ensure a row for this conversation thread; refresh peer address for convenience.
    row, created = await RoleState.get_or_create(
        db,
        defaults={"state": "resp_ready", "peer_address": addr},
        self_id=my_id, role="responder", peer_id=peer_id,
    )
    if created:
        client.logger.info(f"[resp_ready -> resp_confirm] created role_state for peer={peer_id}")
    else:
        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"peer_address": addr})

    if content["intent"] == "register" and content["to"] is None and row.get("local_reference") is None:
        client.logger.info(f"[resp_ready -> resp_confirm] REGISTER | peer_id={peer_id}")
        return Move(Trigger.ok)

    # Reconnect must present our last local_reference as their 'your_ref'
    if content["intent"] == "reconnect" and "your_ref" in content and content["your_ref"] == row.get("local_reference"):
        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_reference": None})
        client.logger.info(f"[resp_ready -> resp_confirm] RECONNECT | peer_id={peer_id} under my_ref={row.get('local_reference')}")
        return Move(Trigger.ok)

@client.receive(route="resp_confirm --> resp_exchange_0")
async def handle_request(payload: dict) -> Optional[Event]:
    """
    First request after confirm (responder side).
      - Require intent == "request" and addressing valid.
      - Require your_nonce and my_nonce.
      - your_nonce must equal our last local_nonce (echo invariant).
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "request" and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[resp_confirm -> resp_exchange_0] intent OK")

    if not("your_nonce" in content and "my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[resp_confirm -> resp_exchange_0] validation OK")

    row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
    client.logger.info(f"[resp_confirm -> resp_exchange_0] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
    if row.get("local_nonce") != content["your_nonce"]:
        return Stay(Trigger.ignore)
    
    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "responder", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[resp_confirm -> resp_exchange_0] received my_nonce={content['my_nonce']!r} previously used")
        return Stay(Trigger.ignore)

    # Accept their my_nonce, reset our local_nonce (we'll generate on send), set exchange_count=1
    await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
        fields={
            "peer_nonce": content["my_nonce"], 
            "local_nonce": None,
            "peer_reference": None,
            "local_reference": None,
            "exchange_count": 1, 
            "peer_address": addr
        })
    await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info("[resp_confirm -> resp_exchange_0] FIRST REQUEST")

    # Added
    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)
    txid = message.get("TXID")
    if txid is None: return Stay(Trigger.ignore)
    await start_trade_with(peer_id, txid)

    return Move(Trigger.ok)

@client.receive(route="resp_exchange_1 --> resp_finalize")
async def handle_request_or_conclude(payload: dict) -> Optional[Event]:
    """
    Respond loop or finalize request (responder side).
      - request  : must have your_nonce/my_nonce; your_nonce must match our last local_nonce.
      - conclude : carries initiator's my_ref; transitions to resp_finalize.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] in ["request", "conclude"] and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[resp_exchange_1 -> resp_finalize] intent OK")

    if not("your_nonce" in content and (("my_nonce" in content and content["intent"] == "request") or
                                        ("my_ref" in content and content["intent"] == "conclude"))):
        return Stay(Trigger.ignore)
    client.logger.info("[resp_exchange_1 -> resp_finalize] validation OK")

    row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
    client.logger.info(f"[resp_exchange_1 -> resp_finalize] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
    if row.get("local_nonce") != content["your_nonce"]:
        return Stay(Trigger.ignore)
    
    if content["intent"] == "conclude":
        # Capture initiator's reference; reset exchange_count; move to resp_finalize
        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
            fields={
                "peer_reference": content["my_ref"], 
                "exchange_count": 0, 
                "peer_address": addr
            })
        client.logger.info("[resp_exchange_1 -> resp_finalize] REQUEST TO CONCLUDE")
        return Move(Trigger.ok)
    
    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "responder", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[resp_exchange_1 -> resp_finalize] received my_nonce={content['my_nonce']!r} previously used")
        return Stay(Trigger.ignore)

    # request: continue ping-pong, bump exchange_count, store their my_nonce, and clear ours
    new_count = int(row.get("exchange_count", 0)) + 1
    await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
        fields={
            "peer_nonce": content["my_nonce"], 
            "local_nonce": None, 
            "exchange_count": new_count, 
            "peer_address": addr
        })
    await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[resp_exchange_1 -> resp_finalize] REQUEST RECEIVED #{new_count}")
    return Stay(Trigger.ok)

@client.receive(route="resp_finalize --> resp_ready")
async def handle_close(payload: dict) -> Optional[Event]:
    """
    Finalization ACK (responder side).
      - Expect initiator's close with both refs.
      - Validate your_ref equals our local_reference (the one we sent as my_ref in finish).
      - On success:
          * Persist peer_reference (initiator's), clear nonces, zero counters.
          * Delete NonceEvent log for this peer (exchange complete).
      - Retry path:
          * If finalize_retry_count exceeds RESP_FINAL_LIMIT, wipe refs and return to ready.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[resp_finalize -> resp_ready] intent OK")

    row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
    if content["intent"] == "close":
        if not("your_ref" in content and "my_ref" in content): return Stay(Trigger.ignore)
        client.logger.info("[resp_finalize -> resp_ready] validation OK")

        client.logger.info(f"[resp_finalize -> resp_ready] check local_reference={row.get('local_reference')!r} ?= your_ref={content['your_ref']!r}")
        if row.get("local_reference") != content["your_ref"]:
            return Stay(Trigger.ignore)

        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
            fields={
                "peer_reference": content["my_ref"],
                "local_nonce": None,
                "peer_nonce": None,
                "finalize_retry_count": 0,
                "exchange_count": 0,
                "peer_address": addr
            })
        # Clear per-peer nonce log after both refs present.
        await NonceEvent.delete(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id})

        client.logger.info(f"[resp_finalize -> resp_ready] CLOSE SUCCESS")

        # Added
        await end_trade_with(peer_id)

        return Move(Trigger.ok)
    
    # Retry path (we didn't see a valid 'close' yet).
    if int(row.get("finalize_retry_count", 0)) > RESP_FINAL_LIMIT:
        # Responder failure -> wipe refs to avoid stale reconnect loops.
        client.logger.warning("[resp_finalize -> resp_ready] FINALIZE RETRY LIMIT REACHED | FAILED TO CLOSE")
        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
            fields={
                "local_nonce": None, 
                "peer_nonce": None,
                "local_reference": None, 
                "peer_reference": None,
                "exchange_count": 0, 
                "finalize_retry_count": 0,
                "peer_address": addr
            })
        return Move(Trigger.error)

    new_retry = int(row.get("finalize_retry_count", 0)) + 1
    await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"finalize_retry_count": new_retry, "peer_address": addr})
    return Stay(Trigger.ok)



""" ======= RECEIVE — BUYER FORK (inside resp_exchange_0; no DB writes) ====== """

@client.receive(route="resp_exchange_0 --> resp_interested")
@decoration_generator(route="resp_exchange_0 --> resp_interested")
async def rx_resp_interested(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)
    
    if message.get("status") == "offer":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)

        if price <= peer_row["limit_acceptable_price"]:
            await set_state_fields(
                db, 
                peer_id,
                current_offer=price,
            )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Interested in {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        else:
            new_offer = peer_row["current_offer"] + peer_row["price_shift"]
            await set_state_fields(
                db, 
                peer_id, 
                current_offer=new_offer
                )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Increased for {peer_id[:10]} at ${new_offer}\033[0m")
            return Stay(Trigger.ok)
    
    return Stay(Trigger.ignore)


@client.receive(route="resp_exchange_0 --> resp_accept")
@decoration_generator(route="resp_exchange_0 --> resp_accept")
async def rx_resp_accept(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_interested":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)
    
        if price <= peer_row["limit_acceptable_price"]:
            await set_state_fields(db, 
                peer_id,
                current_offer=price,
            )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Will accept {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        
    return Stay(Trigger.ignore)


@client.receive(route="resp_exchange_0 --> resp_refuse")
@decoration_generator(route="resp_exchange_0 --> resp_refuse")
async def rx_resp_refuse(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_interested":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)
    
        if price > peer_row["limit_acceptable_price"]:
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Will accept {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        
    return Stay(Trigger.ignore)


@client.receive(route="resp_interested --> resp_exchange_1, resp_accept_too")
@decoration_generator(route="resp_interested --> resp_exchange_1, resp_accept_too")
async def rx_resp_accept_too(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_accept":
        
        price  = message.get("price")
        if price is None: return Stay(Trigger.ignore)

        txid   = message.get("TXID")
        if txid is None: return Stay(Trigger.ignore)

        added = await add_history(db, peer_id, 1, txid)
        client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Accepts also {peer_id[:10]} at ${price}\033[0m")
        if added:
            return Move(Trigger.ok)
        else:
            return Move(Trigger.error)
    return Stay(Trigger.ignore)


@client.receive(route="resp_interested --> resp_exchange_1, resp_refuse_too")
@decoration_generator(route="resp_interested --> resp_exchange_1, resp_refuse_too")
async def rx_resp_refuse_too(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_refuse":
        
        price  = message.get("price")
        if price is None: return Stay(Trigger.ignore)

        txid   = message.get("TXID")
        if txid is None: return Stay(Trigger.ignore)

        added = await add_history(db, peer_id, 0, txid)
        client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Refuse also {peer_id[:10]} at ${price}\033[0m")
        if added:
            return Move(Trigger.ok)
        else:
            return Move(Trigger.error)
    return Stay(Trigger.ignore)


@client.receive(route="resp_accept --> resp_exchange_1")
@decoration_generator(route="resp_accept --> resp_exchange_1")
async def rx_accept_to_merge(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_accept_too":
        
        price  = message.get("price")
        if price is None: return Stay(Trigger.ignore)

        txid   = message.get("TXID")
        if txid is None: return Stay(Trigger.ignore)

        added = await add_history(db, peer_id, 1, txid)
        if added:
            return Move(Trigger.ok)
        else:
            return Move(Trigger.error)
    return Stay(Trigger.ignore)

@client.receive(route="resp_refuse --> resp_exchange_1")
@decoration_generator(route="resp_refuse --> resp_exchange_1")
async def rx_refuse_to_merge(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "selling": return Stay(Trigger.ignore)

    if message.get("status") == "init_refuse_too":
         
        price  = message.get("price")
        if price is None: return Stay(Trigger.ignore)

        txid   = message.get("TXID")
        if txid is None: return Stay(Trigger.ignore)
    
        added = await add_history(db, peer_id, 0, txid)
        if added:
            return Move(Trigger.ok)
        else:
            return Move(Trigger.error)
    return Stay(Trigger.ignore)

""" ============================ SEND DRIVER ================================ """

@client.send(route="sending", multi=True)
async def tick_background_sender() -> list[dict]:
    """
    Periodic outbound driver.
      - Iterates all known peers for responder handshake track.
      - Emits messages that depend solely on RoleState for that (role, peer).
      - Adds a broadcast 'register' each tick for discovery.

    Timing:
      - Sleeps ~1s each tick to simulate paced traffic.
    """
    client.logger.info("[send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for responder role (multi-peer)
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder"})

    # ---------------------------- Responder role ----------------------------
    for row in resp_rows:
        role_state = row.get("state") or "resp_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "resp_finalize":
            # Guard: need peer_reference for your_ref in finish.
            if row.get("peer_reference") is None:
                client.logger.info(f"[send][responder:{role_state}] waiting for peer_reference before finish")
                continue
            # Provide our reference as my_ref; initiator will follow with close.
            local_ref = row.get("local_reference") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_reference": local_ref})
            client.logger.info(f"[send][responder:{role_state}] finish #{row.get('finalize_retry_count', 0)} | my_ref={local_ref}")
            payload = {
                "to": peer_id,
                "intent": "finish",
                "your_ref": row.get("peer_reference"),
                "my_ref": local_ref,
            }

        if payload is not None:
            payloads.append(payload)

    return payloads

@client.send(route="/all --> /all", multi=True, on_triggers = {Trigger.ok, Trigger.error})
async def queued_sender() -> list[dict]:
    """
    Periodic outbound driver.
      - Iterates all known peers for responder handshake track.
      - Emits messages that depend solely on RoleState for that (role, peer).
      - Adds a broadcast 'register' each tick for discovery.

    Timing:
      - Sleeps ~1s each tick to simulate paced traffic.
    """
    client.logger.info("[queued send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for responder role (multi-peer)
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder"})

    # ---------------------------- Responder role ----------------------------
    for row in resp_rows:
        role_state = row.get("state") or "resp_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "resp_confirm":
            # Send confirm with a new my_nonce — initiator must echo as your_nonce.
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][responder:{role_state}] confirm | my_nonce={local_nonce}")
            payload = {"to": peer_id, "intent": "confirm", "my_nonce": local_nonce}

        elif role_state in ["resp_exchange_0", "resp_exchange_1"]:
            # Guard: need peer_nonce for your_nonce field in respond.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][responder:{role_state}] waiting for peer_nonce before respond")
                continue
            # Respond with a fresh my_nonce each round.
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][responder:{role_state}] respond #{row.get('exchange_count', 0)} | my_nonce={local_nonce}")

            peer_row = await get_state(db, peer_id)
            decision = peer_row["agreement"] if peer_row and peer_row["agreement"] else "resp_exchange_0"
            offer = peer_row["current_offer"] if peer_row else None
            txid  = peer_row["transaction_id"] if peer_row else None

            payload = {
                "to": peer_id,
                "intent": "respond",
                "your_nonce": row.get("peer_nonce"),
                "my_nonce": local_nonce,
                "message": {
                    "type": "buying",
                    "status": "offer",
                    "price": offer,
                    "TXID": txid,
                    }
            }
            if decision == "resp_interested":
                payload["message"].update({"status": decision})
                client.logger.info(f"\033[96m[{client.name}|{my_id[:5]}] Interested by {peer_id[:10]} at ${offer}\033[0m")
            
            elif decision.startswith("resp_accept"):
                payload["message"].update({"status": decision})
                if decision.endswith("_too") and role_state == "resp_exchange_1":
                    client.logger.info(f"\033[92m[{client.name}|{my_id[:5]}] Also accepts {peer_id[:10]} at ${offer}\033[0m")
                else:
                    client.logger.info(f"\033[92m[{client.name}|{my_id[:5]}] Accepts {peer_id[:10]} at ${offer}\033[0m")

            elif decision.startswith("resp_refuse"):
                payload["message"].update({"status": decision})
                if decision.endswith("_too") and role_state == "resp_exchange_1":
                    client.logger.info(f"\033[91m[{client.name}|{my_id[:5]}] Also refuses {peer_id[:10]} at ${offer}\033[0m")
                else:
                    client.logger.info(f"\033[91m[{client.name}|{my_id[:5]}] Refuses {peer_id[:10]} at ${offer}\033[0m")
            elif decision == "resp_exchange_0":
                client.logger.info(f"\033[93m[{client.name}|{my_id[:5]}] Offer to {peer_id[:10]} at ${offer}\033[0m")

        if payload is not None:
            payloads.append(payload)

    return payloads



""" =============================== ENTRYPOINT ============================== """

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='Relative path to the client config JSON (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Ensure DB schema before client loop starts.
    client.loop.run_until_complete(setup())

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        asyncio.run(db.close())
