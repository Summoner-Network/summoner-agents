# =============================================================================
# HSAgent_0 — Nonce Handshake (Initiator + Responder)
#
# OVERVIEW
#   Two roles, one agent file. Each (self_id, role, peer_id) has its own state:
#     - Initiator  : init_ready → init_exchange → init_finalize_propose → init_finalize_close → init_ready
#     - Responder  : resp_ready → resp_confirm  → resp_exchange         → resp_finalize        → resp_ready
#
# WIRE MESSAGES (fields are required unless noted)
#   register  : broadcast hello (to=None)
#   confirm   : {to, my_nonce}                          — responder → initiator
#   request   : {to, your_nonce, my_nonce, ...}         — initiator → responder (ping)
#   respond   : {to, your_nonce, my_nonce, ...}         — responder → initiator (pong)
#   conclude  : {to, your_nonce, my_ref}                — initiator → responder (start finalize)
#   finish    : {to, your_ref, my_ref}                  — responder → initiator (provide responder ref)
#   close     : {to, your_ref, my_ref}                  — initiator → responder (ACK finalize)
#   reconnect : {to, your_ref}                          — initiator → responder (resume using responder's last local_reference)
#
# HIGH-LEVEL FLOW
#   1) Discovery / Hello            : register ↔ confirm
#   2) Nonce Ping-Pong              : request/respond (bounded by EXCHANGE_LIMIT)
#   3) Finalize (swap references)   : conclude/finish, then initiator drives close with retries
#   4) Reconnect                    : initiator can rejoin if both sides still hold references
#
# PERSISTENCE MODEL
#   - RoleState(self_id, role, peer_id, ...):
#       state, local_nonce, peer_nonce, local_reference, peer_reference,
#       exchange_count, finalize_retry_count, peer_address
#   - NonceEvent(self_id, role, peer_id, flow∈{'sent','received'}, nonce):
#       per-peer log used for replay protection (dedup on flow='received')
#
# CONCURRENCY MODEL (important!)
#   - We split sending into two loops to avoid races:
#       * tick_background_sender: periodic “maintenance” (register, finalize close/finish, reconnect).
#       * queued_sender: event-driven (on Trigger.ok/error) for the chatty steps (confirm/request/respond/conclude).
#   - Receivers clear local_nonce immediately after accepting a peer nonce.
#     The next my_nonce is minted in queued_sender, guaranteeing we never reuse a stale local_nonce.
#
# INVARIANTS
#   1) Echo check: every request/respond must carry your_nonce == the receiver's last local_nonce.
#   2) Replay check: any inbound my_nonce already seen with flow='received' for this (self,role,peer) is ignored.
#   3) Finalize consistency:
#        - Initiator sends conclude(my_ref).
#        - Responder sends finish(your_ref=peer_reference, my_ref=local_reference).
#        - Initiator repeats close(your_ref=peer_reference, my_ref=local_reference) until retries exceed limit.
#   4) Reconnect: initiator must present responder's last local_reference as your_ref.
#
# TUNABLES
#   - EXCHANGE_LIMIT      : initiator cuts ping-pong after this many cycles.
#   - INIT_FINAL_LIMIT    : initiator stops close loop (refs are preserved for reconnect).
#   - RESP_FINAL_LIMIT    : responder stops waiting for close (refs are wiped to avoid stale reconnects).
# =============================================================================



""" ============================ IMPORTS & TYPES ============================ """
from summoner.client import SummonerClient
from summoner.protocol import Move, Stay, Node, Direction, Event
import argparse
import asyncio
import uuid
import random
from typing import Any, Optional
from pathlib import Path



""" ======================== CONSTANTS & SIMPLE HELPERS ===================== """
# Counters to simulate conversation with several exchanges.
# exchange = alternating request/response rounds before we cut to finalize
# finalize = # of "finish/close" attempts before cutting back to ready
EXCHANGE_LIMIT = 3 # Only for initiator side
INIT_FINAL_LIMIT = 3
RESP_FINAL_LIMIT = 5 # Needs to wait for "conclude"

def generate_random_digits():
    # Nonces/refs are short tokens used for demonstration purposes.
    return ''.join(random.choices('123456789', k=10))

# my agent ID (used in client name and to partition rows in the DB)
my_id = str(uuid.uuid4())



""" =========================== DATABASE WIRING ============================= """

from db_sdk import Database
from db_models import RoleState, NonceEvent

# Each agent instance uses its own on-disk DB. This partitions rows per agent run.
db_path = Path(__file__).resolve().parent / f"HSAgent-{my_id}.db"
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
        - local_nonce / peer_nonce: last local nonce we minted / last peer nonce we accepted
        - local_reference / peer_reference: opaque finalize tokens exchanged in conclude/finish/close
        - exchange_count: per-thread ping-pong counter (initiator bumps on send; responder bumps on receive)
        - finalize_retry_count: counts finalize attempts / wait ticks before cutting
        - peer_address: last observed transport address (diagnostics)
      NonceEvent:
        - flow∈{'sent','received'} and nonce value for replay diagnostics
        - we dedup **only** on flow='received' to reject inbound replays
    """
    await RoleState.create_table(db)
    await NonceEvent.create_table(db)

    await RoleState.create_index(db, "uq_role_peer", ["self_id", "role", "peer_id"], unique=True)
    await RoleState.create_index(db, "ix_role_scan", ["self_id", "role"], unique=False)
    await NonceEvent.create_index(db, "ix_nonce_triplet", ["self_id", "role", "peer_id"], unique=False)



""" ========================= CLIENT & FLOW SETUP =========================== """

client = SummonerClient(name=f"HSAgent_0")

# We activate a flow diagram to orchestrate the client's routes
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")
client_flow.ready()

# Trigger tokens (e.g., ok, ignore, error) used to drive Move/Stay decisions.
Trigger = client_flow.triggers()

# ==== Handshake phases (renamed for clarity) ====
# Initiator: init_ready -> init_exchange -> init_finalize_propose -> init_finalize_close -> init_ready
# Responder: resp_ready -> resp_confirm  -> resp_exchange         -> resp_finalize        -> resp_ready
#
# *_ready         : idle/buffer state
# *_exchange      : alternating nonce ping-pong
# init_finalize_propose: initiator proposes its reference (conclude)
# resp_finalize   : responder returns its reference (finish)
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
          {"initiator:<peer>": <state>, "responder:<peer>": <state>}
      - Each value is the current RoleState.state for that peer or a role-default.
    
    Notes:
      - Keys are strictly peer-scoped; we never advertise global role keys.
      - This snapshot feeds Flow activation; see download() for preference logic.
    """
    peer_id = None
    if isinstance(payload, dict):
        peer_id = payload.get("from") or (payload.get("content", {}) or {}).get("from")

    if peer_id is None:
        # No peer: don't advertise global keys; client will retry with a peer.
        return {}

    # Peer-scoped advertisement, e.g. {"initiator:<peer>": "...", "responder:<peer>": "..."}
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields=["state"])
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields=["state"])

    init_state = init_rows[0]["state"] if init_rows and init_rows[0]["state"] else "init_ready"
    resp_state = resp_rows[0]["state"] if resp_rows and resp_rows[0]["state"] else "resp_ready"

    client.logger.info(f"\033[92m[upload] peer={peer_id[:5]} | initiator={init_state} | responder={resp_state}\033[0m")
    return {f"initiator:{peer_id}": init_state, f"responder:{peer_id}": resp_state}

@client.download_states()
async def download(possible_states: dict[Optional[str], list[Node]]) -> None:
    """
    The runtime tells us what states are currently permissible *per role and peer*.
    We pick one (by our preference order) and store it in RoleState.state.

    Contract:
      - Keys are peer-scoped like "initiator:<peer_id>" or "responder:<peer_id>".
      - We ignore global role-only keys (None or no ':').

    Preference order (why this order?):
      - Initiator:   init_ready > init_finalize_close > init_finalize_propose > init_exchange
          (Prefer reconnect/close completion over starting/continuing exchange.)
      - Responder:   resp_ready > resp_finalize > resp_confirm > resp_exchange
          (Prefer finishing/ack paths before re-confirming or ping-pong.)
    """
    ordered_states = {
        "initiator": ["init_ready", "init_finalize_close", "init_finalize_propose", "init_exchange"],
        "responder": ["resp_ready", "resp_finalize", "resp_confirm", "resp_exchange"],
    }

    for key, role_states in possible_states.items():
        if key is None:
            continue
        if ":" not in str(key):
            # Ignore global per-role keys entirely
            client.logger.info(f"[download] skipping non-scoped key '{key}'")
            continue

        role, peer_id = key.split(":", 1)
        if role not in ("initiator", "responder") or not peer_id:
            continue

        client.logger.info(f"[download] possible states '{key}': {role_states}")

        # Choose first allowed state by our preference
        target_state = next((s for s in ordered_states[role] if Node(s) in role_states), None)
        if not target_state:
            continue

        await RoleState.update(db, where={"self_id": my_id, "role": role, "peer_id": peer_id}, fields={"state": target_state})
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
      - Reconnect note: when your_ref matches our last local_reference we clear it so a fresh
        finalize can proceed; this prevents old refs from pinning us in resp_finalize.

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

@client.receive(route="resp_confirm --> resp_exchange")
async def handle_request(payload: dict) -> Optional[Event]:
    """
    First request after confirm (responder side).
      - Require intent == "request", addressed to us.
      - Require your_nonce and my_nonce.
      - Echo check: your_nonce must equal our last local_nonce.
      - Replay check: reject if my_nonce was already seen with flow='received'.
      - Side effects: accept peer my_nonce, clear local_nonce (so sender mints a fresh one),
        set exchange_count=1 (responder counts on receive), and record NonceEvent(received).
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "request" and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[resp_confirm -> resp_exchange] intent OK")

    if not("your_nonce" in content and "my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[resp_confirm -> resp_exchange] validation OK")

    row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
    client.logger.info(f"[resp_confirm -> resp_exchange] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
    if row.get("local_nonce") != content["your_nonce"]:
        return Stay(Trigger.ignore)
    
    # Replay guard: inbound my_nonce must be new for this (self,role,peer)
    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "responder", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[resp_confirm -> resp_exchange] received my_nonce={content['my_nonce']!r} previously used")
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
    client.logger.info("[resp_confirm -> resp_exchange] FIRST REQUEST")
    return Move(Trigger.ok)

@client.receive(route="resp_exchange --> resp_finalize")
async def handle_request_or_conclude(payload: dict) -> Optional[Event]:
    """
    Respond loop or finalize gate (responder side).
      - request  : {your_nonce, my_nonce} with echo + replay checks. On success:
                   store peer_nonce, clear local_nonce, bump exchange_count, log received.
      - conclude : {your_nonce, my_ref}. On success: capture peer_reference and move to resp_finalize.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] in ["request", "conclude"] and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[resp_exchange -> resp_finalize] intent OK")

    if not("your_nonce" in content and (("my_nonce" in content and content["intent"] == "request") or
                                        ("my_ref" in content and content["intent"] == "conclude"))):
        return Stay(Trigger.ignore)
    client.logger.info("[resp_exchange -> resp_finalize] validation OK")

    row = await ensure_role_state(my_id, "responder", peer_id, "resp_ready")
    client.logger.info(f"[resp_exchange -> resp_finalize] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
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
        client.logger.info("[resp_exchange -> resp_finalize] REQUEST TO CONCLUDE")
        return Move(Trigger.ok)
    
    # Replay guard: inbound my_nonce must be new for this (self,role,peer)
    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "responder", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[resp_exchange -> resp_finalize] received my_nonce={content['my_nonce']!r} previously used")
        return Stay(Trigger.ignore)

    # Request: continue ping-pong, bump exchange_count, store their my_nonce, and clear ours
    new_count = int(row.get("exchange_count", 0)) + 1
    await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
        fields={
            "peer_nonce": content["my_nonce"], 
            "local_nonce": None, 
            "exchange_count": new_count, 
            "peer_address": addr
        })
    await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[resp_exchange -> resp_finalize] REQUEST RECEIVED #{new_count}")
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
          * Any non-close traffic while waiting counts as a “wait tick” (we bump finalize_retry_count).
          * If finalize_retry_count > RESP_FINAL_LIMIT, we wipe both refs and return to resp_ready
            to prevent stale reconnect loops under unreliable transports.

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



""" ===================== RECEIVE HANDLERS — INITIATOR ====================== """

@client.receive(route="init_ready --> init_exchange")
async def handle_confirm(payload: dict) -> Optional[Event]:
    """
    HELLO (initiator side).
      - Expect responder's confirm with my_nonce.
      - Store peer_nonce and proceed to exchange.
      - Side effects: store peer_nonce, clear local_nonce and both references
        so the queued sender can start fresh.
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "confirm" and content["to"] is not None): return
    client.logger.info("[init_ready -> init_exchange] intent OK")

    if not("my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[init_ready -> init_exchange] validation OK")

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
    client.logger.info(f"[init_ready -> init_exchange] peer_nonce set: {content['my_nonce']}")
    return Move(Trigger.ok)

@client.receive(route="init_exchange --> init_finalize_propose")
async def handle_respond(payload: dict) -> Optional[Event]:
    """
    Exchange loop (initiator side).
      - Expect respond with your_nonce/my_nonce.
      - your_nonce must equal our last local_nonce.
      - After EXCHANGE_LIMIT ping-pong cycles, we cut to init_finalize_propose.
      - Replay check: reject if my_nonce was already seen with flow='received'.
      - Side effects: accept peer my_nonce, clear local_nonce (next my_nonce is minted on send),
        and log NonceEvent(received).
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "respond" and content["to"] is not None): return Stay(Trigger.ignore)
    client.logger.info("[init_exchange -> init_finalize_propose] intent OK")

    if not("your_nonce" in content and "my_nonce" in content): return Stay(Trigger.ignore)
    client.logger.info("[init_exchange -> init_finalize_propose] validation OK")

    row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    client.logger.info(f"[init_exchange -> init_finalize_propose] check local_nonce={row.get('local_nonce')!r} ?= your_nonce={content['your_nonce']!r}")
    if row.get("local_nonce") != content["your_nonce"]:
        return Stay(Trigger.ignore)

    # Replay guard: inbound my_nonce must be new for this (self,role,peer)
    seen_my_nonce = await NonceEvent.exists(db, {"self_id": my_id, "role": "initiator", "peer_id": peer_id, "flow": "received", "nonce": content["my_nonce"]})
    if seen_my_nonce:
        client.logger.info(f"[init_exchange -> init_finalize_propose] received my_nonce={content['my_nonce']!r} previously used")
        return Stay(Trigger.ignore)

    if int(row.get("exchange_count", 0)) > EXCHANGE_LIMIT:
        # Accept their nonce, reset counter, proceed to finalize proposal.
        await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
        await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
        client.logger.info(f"[init_exchange -> init_finalize_propose] EXCHANGE CUT (limit reached)")
        return Move(Trigger.ok)

    # Normal path: store peer nonce, clear ours (we'll generate new on send)
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
    await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[init_exchange -> init_finalize_propose] GOT RESPONSE #{row.get('exchange_count', 0)}")
    return Stay(Trigger.ok)

@client.receive(route="init_finalize_propose --> init_finalize_close")
async def handle_finish(payload: dict) -> Optional[Event]:
    """
    Finalize (initiator side).
      - Expect finish carrying your_ref == our local_reference and my_ref (responder's).
      - On success we store peer_reference and clear the NonceEvent log. The initiator then
        drives the close loop (see tick_background_sender).
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
    return Move(Trigger.ok)

@client.receive(route="init_finalize_close --> init_ready")
async def finish_to_idle(payload: dict) -> Optional[Event]:
    """
    Close loop guard (initiator side).
      - If finalize retries exceeded INIT_FINAL_LIMIT, cut back to init_ready.
      - We **preserve** local_reference and peer_reference so reconnect can resume later.
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



""" ============================ SEND DRIVER ================================ """

@client.send(route="sending", multi=True)
async def tick_background_sender() -> list[dict]:
    """
    Background sender (periodic “maintenance”).
      - Initiator: handles reconnect attempts and the close loop (finish ACK retries).
      - Responder: sends finish when in resp_finalize (we already stored peer_reference).
      - Also broadcasts 'register' every tick so new peers can discover us.
      - Sleeps ~1s to simulate paced traffic.
    """
    client.logger.info("[send tick]")
    await asyncio.sleep(1)
    payloads = []

    # Iterate all known peers for both roles (multi-peer)
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator"})
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder"})

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
            # Retry close until we exceed INIT_FINAL_LIMIT; refs are preserved for reconnect.
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
            # Mint local_reference here (not in receive) to avoid races with queued_sender.
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

    # Broadcast a registration each tick so new peers can discover us (low-cost discovery); harmless under high fan-out.
    payloads.append({"to": None, "intent": "register"})
    return payloads

@client.send(route="/all --> /all", multi=True, on_triggers = {Trigger.ok, Trigger.error})
async def queued_sender() -> list[dict]:
    """
    Event-driven sender (hub-spoke).
      - Runs only on receiver Triggers {ok, error}. This guarantees that all receive-side
        state updates (notably local_nonce clearing) are visible before we mint new nonces.
      - Initiator path: drives request cycles and conclude.
      - Responder path: drives confirm/respond cycles.
      - Nonces minted here are logged with flow='sent'; replay protection only checks 'received'.
      - Sleeps ~1s to simulate paced traffic.
    """
    client.logger.info("[queued send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for both roles (multi-peer)
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator"})
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder"})

    # ---------------------------- Initiator role ----------------------------
    for row in init_rows:
        role_state = row.get("state") or "init_ready"
        peer_id    = row["peer_id"]
        payload = None

        if role_state == "init_exchange":
            # Guard: must have peer_nonce to echo back as your_nonce.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before first request")
                continue
            # Mint next my_nonce after receive cleared local_nonce; bump initiator exchange_count on send.
            new_cnt = int(row.get("exchange_count", 0)) + 1
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"local_nonce": local_nonce, "exchange_count": new_cnt})
            await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][initiator:{role_state}] request #{new_cnt} | my_nonce={local_nonce}")
            payload = {
                "to": peer_id,
                "intent": "request",
                "your_nonce": row.get("peer_nonce"),
                "my_nonce": local_nonce,
                "message": "How are you?"
            }

        elif role_state == "init_finalize_propose":
            # Guard: must have peer_nonce to echo in conclude.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before conclude")
                continue
            # Mint next my_nonce after receive cleared local_nonce; bump initiator finalize_retry_count on send.
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

    # ---------------------------- Responder role ----------------------------
    for row in resp_rows:
        role_state = row.get("state") or "resp_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "resp_confirm":
            # Mint next my_nonce after receive cleared local_nonce
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][responder:{role_state}] confirm | my_nonce={local_nonce}")
            payload = {"to": peer_id, "intent": "confirm", "my_nonce": local_nonce}

        elif role_state == "resp_exchange":
            # Guard: need peer_nonce for your_nonce field in respond.
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][responder:{role_state}] waiting for peer_nonce before respond")
                continue
            # Mint next my_nonce after receive cleared local_nonce; responder bumps exchange_count on receive only.
            local_nonce = row.get("local_nonce") or generate_random_digits()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][responder:{role_state}] respond #{row.get('exchange_count', 0)} | my_nonce={local_nonce}")
            payload = {
                "to": peer_id,
                "intent": "respond",
                "your_nonce": row.get("peer_nonce"),
                "my_nonce": local_nonce,
                "message": "I am OK!"
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
