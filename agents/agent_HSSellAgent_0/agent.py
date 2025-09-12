# =============================================================================
# HSAgent_0 — Handshake + Nonce Exchange Demo
#
# OVERVIEW
#   Two roles:
#     - Initiator  : init_ready → init_exchange → init_finalize_propose → init_finalize_close → init_ready
#
#   High-level:
#     1) Discovery / Hello            : register ↔ confirm
#     2) Nonce Ping-Pong              : request/respond (bounded by EXCHANGE_LIMIT)
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
#   - EXCHANGE_LIMIT: number of ping-pong rounds before cutting to finalize.
#   - INIT_FINAL_LIMIT   : number of finalize retries before cutting back to ready.
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
EXCHANGE_LIMIT = 3 # Only for initiator side
INIT_FINAL_LIMIT = 3

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
    set_state_fields, add_history, show_statistics, start_negotiation_seller,
)

# Each agent instance uses its own on-disk DB. This partitions rows per agent run.
db_path = Path(__file__).resolve().parent / f"HSSellAgent-{my_id}.db"
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

client = SummonerClient(name=f"HSSellAgent_0")

# We activate a flow diagram to orchestrate the client's routes
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")
client_flow.ready()

# Trigger tokens (e.g., ok, ignore, error) used to drive Move/Stay decisions.
Trigger = client_flow.triggers()

# ==== Handshake phases (renamed for clarity) ====
# Initiator: init_ready -> init_exchange -> init_finalize_propose -> init_finalize_close -> init_ready
#
# *_ready         : idle/buffer state
# *_exchange      : alternating nonce ping-pong
# init_finalize_propose: initiator proposes its reference (conclude)
# init_finalize_close: initiator repeats close until counter exceeds INIT_FINAL_LIMIT



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

async def start_trade_with(peer_id):
    await create_or_reset_state(db, peer_id)
    peer_row = await get_state(db, peer_id)
    if peer_row["transaction_id"] is None:
        txid = await start_negotiation_seller(db, peer_id)
        peer_row = await get_state(db, peer_id)
        client.logger.info(
            f"\033[94m[{client.name}|{my_id[:5]}] Started with {peer_id[:10]}. "
            f"MIN={peer_row['limit_acceptable_price']}, "
            f"OFFER={peer_row['current_offer']}, TXID={txid}\033[0m"
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

            if not(content["intent"] == "respond" and content["to"] is not None): return Stay(Trigger.ignore)
            client.logger.info(f"[{route}] intent OK")

            if not("your_nonce" in content and "my_nonce" in content): return Stay(Trigger.ignore)
            client.logger.info(f"[{route}] validation OK")

            row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
            client.logger.info(f"[{route}] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
            if row.get("local_nonce") != content["your_nonce"]:
                return Stay(Trigger.ignore)
            
            seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "initiator", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
            if seen_my_nonce:
                client.logger.info(f"[{route}] received my_nonce={content['my_nonce']!r} previously used")
                return Stay(Trigger.ignore)
            
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
            await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
            client.logger.info(f"[{route}] GOT RESPONSE #{row.get('exchange_count', 0)}")

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
          {"initiator:<peer>": <state>, "seller:<peer>": <state>}
      - Each value is the current RoleState.state for that peer or a role-default.
    """
    peer_id = None
    if isinstance(payload, dict):
        peer_id = payload.get("from") or (payload.get("content", {}) or {}).get("from")

    if peer_id is None:
        # No peer: don't advertise global keys; client will retry with a peer.
        return {}

    # Peer-scoped advertisement, e.g. {"initiator:<peer>": "...", "seller:<peer>": "..."}
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields=["state"])

    init_state = init_rows[0]["state"] if init_rows and init_rows[0]["state"] else "init_ready"

    # Negotiation view
    peer_row = await get_state(db, peer_id)

    seller_state = peer_row["agreement"] if peer_row and peer_row["agreement"] else "init_exchange_0"

    # Merge / Fork logic
    if init_state == "init_exchange_0":
        state_dict = {f"seller:{peer_id}": seller_state}
    else:
        state_dict = {f"initiator:{peer_id}": init_state}

    client.logger.info(f"\033[92m[upload] peer={peer_id[:5]} | {state_dict}\033[0m")
    return state_dict

@client.download_states()
async def download(possible_states: dict[Optional[str], list[Node]]) -> None:
    """
    The runtime tells us what states are currently permissible *per role and peer*.
    We pick one (by our preference order) and store it in RoleState.state.

    Contract:
      - Keys are peer-scoped like "initiator:<peer_id>" or "seller:<peer_id>".
      - We ignore global role-only keys (None or no ':').

    Preference order:
      - Initiator:   init_ready > init_finalize_close > init_finalize_propose > init_exchange_1 > init_exchange_0
      - Seller   :   init_refuse_too > init_refuse > init_accept_too > init_accept > init_interested > init_exchange_0
    """
    ordered_states = {
        "initiator": ["init_ready", "init_finalize_close", "init_finalize_propose", "init_exchange_1", "init_exchange_0"],
        "seller":    ["init_refuse_too", "init_refuse", "init_accept_too", "init_accept", "init_interested", "init_exchange_0"],    
    }

    for key, role_states in possible_states.items():
        if key is None:
            continue
        if ":" not in str(key):
            # Ignore global per-role keys entirely
            client.logger.info(f"[download] skipping non-scoped key '{key}'")
            continue

        role, peer_id = key.split(":", 1)
        if role not in ("initiator", "seller") or not peer_id:
            continue

        client.logger.info(f"[download] possible states '{key}': {role_states}")

        # Choose first allowed state by our preference
        target_state = next((s for s in ordered_states[role] if Node(s) in role_states), None)

        if role == "initiator" and target_state:
            await RoleState.update(db, where={"self_id": my_id, "role": role, "peer_id": peer_id}, fields={"state": target_state})

        if role == "seller":
            if target_state:
                await set_state_fields(db, peer_id, agreement=target_state)

            if Node("init_exchange_1") in role_states:
                await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"state": "init_exchange_1"})
                client.logger.info(f"[download] '{role}' changed 'initiator' -> 'init_exchange_1' for {peer_id[:5]}")

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



""" ===================== RECEIVE HANDLERS — INITIATOR ====================== """

@client.receive(route="init_ready --> init_exchange_0")
async def handle_confirm(payload: dict) -> Optional[Event]:
    """
    HELLO (initiator side).
      - Expect responder's confirm with my_nonce.
      - Store peer_nonce and proceed to exchange.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "confirm" and content["to"] is not None): return
    client.logger.info("[init_ready -> init_exchange_0] intent OK")

    if not("my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[init_ready -> init_exchange_0] validation OK")

    await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id},
        fields={
            "peer_nonce": content["my_nonce"], 
            "exchange_count": 0,
            "local_nonce": None,
            "peer_reference": None,
            "local_reference": None,
            "peer_address": addr
        })
    await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[init_ready -> init_exchange_0] peer_nonce set: {content['my_nonce']}")

    # Added
    await start_trade_with(peer_id)

    return Move(Trigger.ok)

@client.receive(route="init_exchange_1 --> init_finalize_propose")
async def handle_respond(payload: dict) -> Optional[Event]:
    """
    Exchange loop (initiator side).
      - Expect respond with your_nonce/my_nonce.
      - your_nonce must equal our last local_nonce.
      - If exchange_count > EXCHANGE_LIMIT, cut to finalize_propose.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "respond" and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[init_exchange_1 -> init_finalize_propose] intent OK")

    if not("your_nonce" in content and "my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[init_exchange_1 -> init_finalize_propose] validation OK")

    row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    client.logger.info(f"[init_exchange_1 -> init_finalize_propose] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
    if row.get("local_nonce") != content["your_nonce"]:
        return Stay(Trigger.ignore)

    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "initiator", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[init_exchange_1 -> init_finalize_propose] received my_nonce={content['my_nonce']!r} previously used")
        return Stay(Trigger.ignore)

    if int(row.get("exchange_count", 0)) > EXCHANGE_LIMIT:
        # Accept their nonce, reset counter, proceed to finalize proposal.
        await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
        await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
        client.logger.info(f"[init_exchange_1 -> init_finalize_propose] EXCHANGE CUT (limit reached)")
        return Move(Trigger.ok)

    # Normal path: store peer nonce, clear ours (we'll generate new on send)
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
    await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[init_exchange_1 -> init_finalize_propose] GOT RESPONSE #{row.get('exchange_count', 0)}")
    return Stay(Trigger.ok)

@client.receive(route="init_finalize_propose --> init_finalize_close")
async def handle_finish(payload: dict) -> Optional[Event]:
    """
    Finalize (initiator side).
      - Expect finish carrying your_ref == our local_reference and my_ref (responder's).
      - On success: persist peer_reference, clear nonce log, proceed to close loop.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "finish" and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[init_finalize_propose -> init_finalize_close] intent OK")

    if not("your_ref" in content and "my_ref" in content): return Stay(Trigger.ignore)
    client.logger.info("[init_finalize_propose -> init_finalize_close] validation OK")

    row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    client.logger.info(f"[init_finalize_propose -> init_finalize_close] check local_reference={row.get('local_reference')!r} ?= your_ref={content['your_ref']!r}")
    if row.get("local_reference") != content["your_ref"]:
        return Stay(Trigger.ignore)

    # Success: capture responder's ref; clear transient nonce log.
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, 
            fields={
                "peer_reference": content["my_ref"], 
                "finalize_retry_count": 0, 
                "peer_address": addr
            })
    # Clear per-peer nonce log after both refs present.
    await NonceEvent.delete(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id})
    client.logger.info("[init_finalize_propose -> init_finalize_close] CLOSE")

    # Added
    await end_trade_with(peer_id)

    return Move(Trigger.ok)

@client.receive(route="init_finalize_close --> init_ready")
async def finish_to_idle(payload: dict) -> Optional[Event]:
    """
    Close loop guard (initiator side).
      - If finalize retries exceeded INIT_FINAL_LIMIT, cut to ready but retain refs for reconnect.
    """
    content = payload["content"]
    peer_id = content["from"]

    if peer_id is None: return Stay(Trigger.ignore)

    row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    if int(row.get("finalize_retry_count", 0)) > INIT_FINAL_LIMIT:
        await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id},
            fields={
                "local_nonce": None,
                "peer_nonce": None,
                # keep local_reference / peer_reference
                "exchange_count": 0,
                "finalize_retry_count": 0
            })
        client.logger.info("[init_finalize_close -> init_ready] CUT (refs preserved)")
        return Move(Trigger.ok)

    return Stay(Trigger.ok)


""" ========== RECEIVE HANDLERS — SELLER NEGOTIATION (FORK/MERGE) ========== """


@client.receive(route="init_exchange_0 --> init_interested")
@decoration_generator(route="init_exchange_0 --> init_interested")
async def rx_init_interested(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)
    
    if message.get("status") == "offer":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)

        if price >= peer_row["limit_acceptable_price"]:
            await set_state_fields(
                db, 
                peer_id,
                current_offer=price,
            )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Interested in {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        else:
            new_offer = peer_row["current_offer"] - peer_row["price_shift"]
            await set_state_fields(
                db, 
                peer_id, 
                current_offer=new_offer
                )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Decreased for {peer_id[:10]} at ${new_offer}\033[0m")
            return Stay(Trigger.ok)
    
    return Stay(Trigger.ignore)


@client.receive(route="init_exchange_0 --> init_accept")
@decoration_generator(route="init_exchange_0 --> init_accept")
async def rx_init_accept(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_interested":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)
    
        if price >= peer_row["limit_acceptable_price"]:
            await set_state_fields(db, 
                peer_id,
                current_offer=price,
            )
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Will accept {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        
    return Stay(Trigger.ignore)


@client.receive(route="init_exchange_0 --> init_refuse")
@decoration_generator(route="init_exchange_0 --> init_refuse")
async def rx_init_refuse(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_interested":
        
        price = message.get("price")
        if price is None: return Stay(Trigger.ignore)
        peer_row = await get_state(db, peer_id)
    
        if price < peer_row["limit_acceptable_price"]:
            client.logger.info(f"\033[90m[{client.name}|{my_id[:5]}] Will refuse {peer_id[:10]} at ${price}\033[0m")
            return Move(Trigger.ok)
        
    return Stay(Trigger.ignore)


@client.receive(route="init_interested --> init_exchange_1, init_accept_too")
@decoration_generator(route="init_interested --> init_exchange_1, init_accept_too")
async def rx_init_accept_too(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_accept":
        
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


@client.receive(route="init_interested --> init_exchange_1, init_refuse_too")
@decoration_generator(route="init_interested --> init_exchange_1, init_refuse_too")
async def rx_init_refuse_too(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_refuse":
        
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


@client.receive(route="init_accept --> init_exchange_1")
@decoration_generator(route="init_accept --> init_exchange_1")
async def rx_accept_to_merge(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_accept_too":
        
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

@client.receive(route="init_refuse --> init_exchange_1")
@decoration_generator(route="init_refuse --> init_exchange_1")
async def rx_refuse_to_merge(payload: dict) -> Optional[Event]:
    
    content = payload["content"]
    peer_id = content["from"]

    if not isinstance(content, dict) or "message" not in content: return Stay(Trigger.ignore)
    message = content["message"]
    if message.get("type") != "buying": return Stay(Trigger.ignore)

    if message.get("status") == "resp_refuse_too":

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
      - Iterates all known peers for initiator handshake track.
      - Emits messages that depend solely on RoleState for that (role, peer).
      - Adds a broadcast 'register' each tick for discovery.

    Timing:
      - Sleeps ~1s each tick to simulate paced traffic.
    """
    client.logger.info("[send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for initiator role (multi-peer)
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator"})

    # ---------------------------- Initiator role ----------------------------
    for row in init_rows:
        role_state = row.get("state") or "init_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "init_ready":
            # Reconnect path: only if we remember peer_reference from prior finalize.
            if peer_id and row.get("peer_reference"):
                client.logger.info(f"[send][initiator:{role_state}] reconnect with {peer_id} under {row.get('peer_reference')}")
                payload = {"to": peer_id, "your_ref": row.get("peer_reference"), "intent": "reconnect"}

        elif role_state == "init_finalize_close":
            # Guard: cannot send close until both refs are known.
            if row.get("peer_reference") is None or row.get("local_reference") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for refs before close")
                continue
            # Send close repeatedly until counter exceeded INIT_FINAL_LIMIT.
            if int(row.get("finalize_retry_count", 0)) > INIT_FINAL_LIMIT:
                await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id},
                    fields={
                        "local_nonce": None,
                        "peer_nonce": None,
                        # keep local_reference / peer_reference
                        "state": "init_ready",
                        "exchange_count": 0,
                        "finalize_retry_count": 0
                    })
                client.logger.info("[init_finalize_close -> init_ready] CUT (refs preserved)")
            else:
                new_retry = int(row.get("finalize_retry_count", 0)) + 1
                await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"finalize_retry_count": new_retry})
                client.logger.info(f"[send][initiator:{role_state}] close #{new_retry} | your_ref={row.get('peer_reference')}")
                payload = {
                    "to": peer_id,
                    "intent": "close",
                    "your_ref": row.get("peer_reference"),
                    "my_ref": row.get("local_reference"),
                }

        if payload is not None:
            payloads.append(payload)


    # Broadcast a registration each tick so new peers can discover us.
    payloads.append({"to": None, "intent": "register"})
    return payloads

@client.send(route="/all --> /all", multi=True, on_triggers = {Trigger.ok, Trigger.error})
async def queued_sender() -> list[dict]:
    """
    Periodic outbound driver.
      - Iterates all known peers for initiator handshake track.
      - Emits messages that depend solely on RoleState for that (role, peer).
      - Adds a broadcast 'register' each tick for discovery.

    Timing:
      - Sleeps ~1s each tick to simulate paced traffic.
    """
    client.logger.info("[queued send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for initiator role (multi-peer)
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator"})

    # ---------------------------- Initiator role ----------------------------
    for row in init_rows:
        role_state = row.get("state") or "init_ready"
        peer_id    = row["peer_id"]
        payload = None

        if role_state in ["init_exchange_0", "init_exchange_1"]:
            # Guard: must have peer_nonce to echo back as your_nonce.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before first request")
                continue
            # Generate my_nonce and bump exchange_count.
            new_cnt = int(row.get("exchange_count", 0)) + 1
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"local_nonce": local_nonce, "exchange_count": new_cnt})
            await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][initiator:{role_state}] request #{new_cnt} | my_nonce={local_nonce}")

            peer_row = await get_state(db, peer_id)
            decision = peer_row["agreement"] if peer_row and peer_row["agreement"] else "init_exchange_0"
            offer = peer_row["current_offer"] if peer_row else None
            txid  = peer_row["transaction_id"] if peer_row else None

            payload = {
                "to": peer_id,
                "intent": "request",
                "your_nonce": row.get("peer_nonce"),
                "my_nonce": local_nonce,
                "message": {
                    "type": "selling",
                    "status": "offer",
                    "price": offer,
                    "TXID": txid,
                    }
            }
            if decision == "init_interested":
                payload["message"].update({"status": decision})
                client.logger.info(f"\033[96m[{client.name}|{my_id[:5]}] Interested by {peer_id[:10]} at ${offer}\033[0m")
            
            elif decision.startswith("init_accept"):
                payload["message"].update({"status": decision})
                if decision.endswith("_too") and role_state == "init_exchange_1":
                    client.logger.info(f"\033[92m[{client.name}|{my_id[:5]}] Also accepts {peer_id[:10]} at ${offer}\033[0m")
                else:
                    client.logger.info(f"\033[92m[{client.name}|{my_id[:5]}] Accepts {peer_id[:10]} at ${offer}\033[0m")

            elif decision.startswith("init_refuse"):
                payload["message"].update({"status": decision})
                if decision.endswith("_too") and role_state == "init_exchange_1":
                    client.logger.info(f"\033[91m[{client.name}|{my_id[:5]}] Also refuses {peer_id[:10]} at ${offer}\033[0m")
                else:
                    client.logger.info(f"\033[91m[{client.name}|{my_id[:5]}] Refuses {peer_id[:10]} at ${offer}\033[0m")
            elif decision == "init_exchange_0":
                client.logger.info(f"\033[93m[{client.name}|{my_id[:5]}] Offer to {peer_id[:10]} at ${offer}\033[0m")

        elif role_state == "init_finalize_propose":
            # Guard: must have peer_nonce to echo in conclude.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before conclude")
                continue
            # Propose our reference (my_ref). Track finalize retries.
            new_retry = int(row.get("finalize_retry_count", 0)) + 1
            local_ref = row.get("local_reference") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"local_reference": local_ref, "finalize_retry_count": new_retry})
            client.logger.info(f"[send][initiator:{role_state}] conclude #{new_retry} | my_ref={local_ref}")
            payload = {
                "to": peer_id,
                "intent": "conclude",
                "your_nonce": row.get("peer_nonce"),
                "my_ref": local_ref,
            }

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
