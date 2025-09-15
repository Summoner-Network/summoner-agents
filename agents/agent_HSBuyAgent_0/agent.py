# =============================================================================
# HSBuyAgent_0 — Responder Handshake + Buyer Decision Track
#
# OVERVIEW
#   Two coordinated tracks in a single-role agent:
#
#   Handshake (responder):
#     resp_ready → resp_confirm → resp_exchange_0 → resp_exchange_1 → resp_finalize → resp_ready
#
#   Buyer (negotiation overlay during exchange):
#     Fork from resp_exchange_0 based on seller messages:
#       resp_exchange_0 → resp_interested
#       resp_exchange_0 → resp_accept
#       resp_exchange_0 → resp_refuse
#     Merge into handshake’s resp_exchange_1 when counterpart confirms:
#       resp_interested → resp_exchange_1, resp_accept_too
#       resp_interested → resp_exchange_1, resp_refuse_too
#       resp_accept     → resp_exchange_1
#       resp_refuse     → resp_exchange_1
#
#   High-level:
#     1) Discovery / Hello            : register ↔ confirm
#     2) Nonce Ping-Pong              : request/respond (length driven by the initiator’s EXCHANGE_LIMIT)
#        • Buyer decisions ride in content["message"] (compact JSON)
#     3) Finalize (swap references)   : conclude/finish, then the initiator drives close
#     4) Reconnect                    : initiator can resume if both sides remember refs
#
# WIRE MESSAGES
#   Handshake intents: register, confirm, request, respond, conclude, finish, close, reconnect
#   Negotiation payload (inside content["message"]):
#     Outbound (buyer  → seller): {"type":"buying" , "status": <buyer_state> , "price": <float>, "TXID": <uuid?>}
#     Inbound  (seller → buyer ): {"type":"selling", "status": <seller_state>, "price": <float>, "TXID": <uuid?>}
#
# PERSISTENCE
#   RoleState(self_id, role="responder", peer_id): state, nonces, references, counters, peer_address
#   NonceEvent(self_id, role, peer_id, flow ∈ {"sent","received"}, nonce): replay diagnostics (cleared on close success)
#   TradeState(peer_id): agreement (buyer decision), pricing fields, transaction_id (seeded from seller TXID)
#   History(agent_id, peer_id, txid, outcome): accept/refuse confirmations
#
# STATE ADVERTISING (peer-scoped)
#   - While in resp_exchange_0, upload advertises {"buyer:<peer>": <agreement_or_default>}
#   - Otherwise it advertises {"responder:<peer>": <handshake_state>}
#   - download() applies role-specific preferences and may proactively merge responder to resp_exchange_1.
#
# CONCURRENCY MODEL
#   - tick_background_sender: periodic maintenance (emits finish during resp_finalize).
#   - queued_sender        : event-driven after receives (confirm/respond), avoiding nonce races.
#
# INVARIANTS
#   1) Echo: every request/respond carries your_nonce == receiver’s last local_nonce.
#   2) Replay: inbound my_nonce previously seen with flow="received" is ignored.
#   3) Finalize sequence:
#        initiator: conclude(my_ref)
#        responder: finish(your_ref=peer_reference, my_ref=local_reference)
#        initiator: close(your_ref=peer_reference, my_ref=local_reference)
#   4) Reconnect: initiator must present responder’s last local_reference as your_ref.
#   5) Buyer overlay never bypasses handshake checks; decoration_generator enforces them first.
#
# TUNABLES
#   RESP_FINAL_LIMIT : responder keeps offering finish and waits for a valid close until this limit is exceeded.
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
    """
    Initialize or refresh TradeState for this peer after the first valid request.
    Uses the seller-provided TXID; sets bounds/current_offer and logs parameters.
    """
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
    """
    Emit per-peer negotiation statistics (success rate, last TXID) and clear
    agreement/transaction_id so a new trade can start cleanly later.
    """
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
        """
        Handshake-first guard for buyer routes driven by seller 'request' messages.

        Enforces before negotiation logic:
        - intent == "request" and addressed to us
        - presence of your_nonce and my_nonce
        - echo check (your_nonce == our last local_nonce)
        - replay drop (inbound my_nonce unseen for flow="received")
        - side effects: store peer_nonce, clear local_nonce, bump exchange_count,
            log NonceEvent(received)

        After invariants, delegates to the decorated function to update TradeState/History
        and decide the fork/merge transition for the buyer track.
        """
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
    Peer-scoped advertisement for BOTH tracks.

    If no peer is known, return {}.
    Otherwise return exactly one key:
      • {"buyer:<peer>": <agreement_or_default>}   when responder is in resp_exchange_0
      • {"responder:<peer>": <handshake_state>}    for all other responder states

    This lets the counterpart fork/merge the buyer overlay while the responder
    continues controlling the handshake progression.
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
    Apply allowed states per peer for BOTH tracks with role-specific preferences.

    Keys:
      "responder:<peer_id>" or "buyer:<peer_id>" (ignore global role-only keys).

    Preference:
      Responder: resp_ready > resp_finalize > resp_confirm > resp_exchange_1 > resp_exchange_0
      Buyer    : resp_refuse_too > resp_refuse > resp_accept_too > resp_accept > resp_interested > resp_exchange_0

    Effects:
      - For responder: persist the chosen handshake state in RoleState.state.
      - For buyer    : persist TradeState.agreement; if resp_exchange_1 is allowed on
        the buyer side, proactively merge responder to resp_exchange_1.
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
    HELLO / reconnect gate (responder).
      - Accept a fresh 'register' when to is None and no local_reference is held.
      - Accept 'reconnect' when your_ref matches our last local_reference; clear it to allow a fresh finalize.
      - Always ensure the RoleState row exists and refresh peer_address.
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
    First request after confirm (responder).
      - Require request addressed to us with your_nonce/my_nonce.
      - Echo + replay checks; on success store peer my_nonce, clear local_nonce,
        set exchange_count=1, and log NonceEvent(flow="received").
      - Start buyer track for this peer using the seller's TXID (from content["message"]).
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
    Exchange loop or finalize gate (responder).
      - request  : echo + replay checks, accept peer my_nonce, clear local_nonce,
                   bump exchange_count, log NonceEvent(received), stay in exchange.
      - conclude : capture initiator's my_ref as peer_reference, reset exchange_count,
                   and move to resp_finalize.
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
    Finalization ACK (responder).
      - Expect initiator's close(your_ref,my_ref) with your_ref == our local_reference.
      - On success: persist peer_reference, clear NonceEvent for this peer, zero counters,
        log CLOSE SUCCESS, and end the buyer trade (emit stats + clear TX metadata).
      - Retry path: if finalize_retry_count exceeds RESP_FINAL_LIMIT, wipe refs and return to ready.
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



""" ========== RECEIVE — BUYER FORK/MERGE (updates TradeState/History) ========= """
# - Each handler is wrapped by decoration_generator(route), so handshake invariants
#   (addressing, intent, nonce echo, replay drop) are enforced before any trading logic runs.
# - Forks from resp_exchange_0 into: resp_interested / resp_accept / resp_refuse.
# - Merges into handshake’s resp_exchange_1 when the seller confirms: *_too routes or direct merges.
# - Handlers update TradeState.agreement/current_offer and append to History on confirmations.

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
    Background sender (handshake maintenance for responder).
      - In resp_finalize: send finish(your_ref=peer_reference, my_ref=<local_ref>).
      - Wait until peer_reference is known (set on conclude).

    Timing: sleeps ~1s to pace traffic.
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
    Event-driven sender for BOTH tracks (runs on receiver triggers {ok, error}).

      Handshake (responder):
        - In resp_confirm: mint my_nonce, log flow="sent", and send confirm(my_nonce).
        - In resp_exchange_0/1: mint my_nonce and send respond(your_nonce=peer_nonce, my_nonce=...).

      Buyer (overlay):
        - Each respond carries content["message"] derived from TradeState:
            {"type":"buying","status": <agreement_or_default>,"price": <current_offer>,"TXID": <transaction_id>}
          Status is:
            • "offer" (default) or "resp_interested" while in resp_exchange_0
            • "resp_accept" / "resp_refuse" or their *_too variants when merging via resp_exchange_1
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
