# =============================================================================
# HSAgent_1 — DID + Crypto Handshake Demo
#
# OVERVIEW
#   This variant extends the basic handshake with DID-style identity material:
#     - Long-term identity file (encrypted) containing:
#         * my_id   : stable agent identifier (UUID string here)
#         * kx_priv : X25519 private key for ECDH (key agreement)
#         * sign_priv: Ed25519 private key for signing
#     - A signed "handshake blob" (hs) exchanged at the beginning to:
#         * prove possession of sign_priv
#         * include an ephemeral public key for KX
#         * bind to a fresh nonce for replay protection
#     - A derived symmetric key (SYM_KEYS[(role, peer)]) for optional message sealing.
#
# MESSAGE LAYERS
#   Plain fields:
#     - "intent", "to", "from", "my_nonce", "your_nonce", "my_ref", "your_ref"
#   Crypto fields:
#     - "hs"  : signed handshake blob with KX pubkey, signature, and a bound nonce
#     - "sec" : sealed envelope {message: "..."} signed+MACed with the derived SYM key
#
# STATE & STORAGE
#   - RoleState rows track per-thread state, nonces, references, counters, peer addr.
#   - NonceEvent logs nonces {sent|received}, also reused as a replay/TTL store for hs.
#   - SYM_KEYS[(role, peer)] and PEER_SIGN_PUB[(role, peer)] live in RAM.
#   - Optional persistence via persist_crypto_meta (best-effort, safe if columns exist).
#
# INVARIANTS
#   1) Echo rule (nonces):
#        request/respond must carry your_nonce == last counterpart local_nonce
#   2) Finalize rule (refs):
#        conclude(my_ref) → finish(your_ref,my_ref) → close(your_ref,my_ref)
#   3) Handshake rule (hs):
#        - The initiator attaches hs on the first request.
#        - The responder attaches hs on confirm.
#        - validate_handshake_message(...) checks signature, nonce freshness, and derives SYM key.
#
# SECURITY NOTES
#   - HS nonces are inserted in NonceEvent for replay defense; a TTL backs cleanup.
#   - Encrypted identity file storage: load_identity_json_encrypted / save_identity_json_encrypted.
#   - Envelope sealing uses sign+MAC with a derived symmetric key; open_envelope verifies and decrypts.
#
# TUNABLES
#   - EXCHANGE_LIMIT, FINAL_LIMIT: bound exchange loops and finalize retries.
#   - DBNonceStore.ttl_seconds: window for handshake nonce replays.
# =============================================================================


""" ============================ IMPORTS & TYPES ============================ """
import json
from summoner.client import SummonerClient
from summoner.protocol import Move, Stay, Node, Direction, Event
import argparse
import asyncio
import uuid
import random
from typing import Any, Optional
from pathlib import Path

import argparse

# ---[ CRYPTO ADDITIONS ]---
# Per-agent KX + signing keys; handshake helpers.
import datetime as _dt
import secrets
from cryptography.hazmat.primitives.asymmetric import x25519, ed25519
from crypto_utils import (
    seal_envelope, open_envelope,
    build_handshake_message,
    validate_handshake_message,
    load_identity_json_encrypted, save_identity_json_encrypted
)



""" ======================== CONSTANTS & SIMPLE HELPERS ===================== """
# Counters to simulate conversation with several exchanges.
# exchange = alternating request/response rounds before we cut to finalize
# finalize = # of "finish/close" attempts before cutting back to ready
EXCHANGE_LIMIT = 3
FINAL_LIMIT = 3

def generate_nonce() -> str:
    # 32 hex chars (128 bits) - cryptographically strong
    return secrets.token_hex(16)

def generate_reference() -> str:
    # Refs are short tokens used for demonstration purposes.
    return ''.join(random.choices('123456789', k=5))



""" ============================ DID & IDENTITY ============================= """

# Command-line identity selector (so you can run multiple agents concurrently).
id_parser = argparse.ArgumentParser()
id_parser.add_argument("--name", required=True, help="Short agent tag (e.g. 1, 2, 3, alice)")
id_args, _ = id_parser.parse_known_args()
IDENT_PATH = Path(__file__).resolve().parent / f"id_agent_{id_args.name}.json"
IDENT_PASSWORD = f"my_id_pw_{id_args.name}".encode()  # choose a strong passphrase in real usage

# Identity file contains: my_id, kx_priv, sign_priv (encrypted at rest).
try:
    my_id, kx_priv, sign_priv, _, _ = load_identity_json_encrypted(str(IDENT_PATH), IDENT_PASSWORD)
    print("[identity] loaded existing identity (encrypted)")
except FileNotFoundError:
    my_id = str(uuid.uuid4())
    kx_priv   = x25519.X25519PrivateKey.generate()
    sign_priv = ed25519.Ed25519PrivateKey.generate()
    save_identity_json_encrypted(str(IDENT_PATH), IDENT_PASSWORD, my_id, kx_priv, sign_priv)
    print("[identity] generated new identity and saved (encrypted)")

# In-RAM per-peer crypto context (derived after validating the first signed message)
# Keyed by (role, peer_id)
SYM_KEYS: dict[tuple[str, str], bytes] = {}
PEER_SIGN_PUB: dict[tuple[str, str], str] = {}

# Optional: attempt to persist crypto metadata; leave ON (safe if columns exist, no-op otherwise)
PERSIST_CRYPTO = True



""" ============================= DATABASE WIRING =========================== """

from db_sdk import Database
from db_models import RoleState, NonceEvent
from adapt import SummonerAPIClient
from selftest import runSelfTests

db_path = Path(__file__).resolve().parent / f"HSAgent-{my_id}.db"
db = Database(db_path)

async def setup() -> None:
    """
    Create tables and the indexes we rely on for uniqueness and scanning.

    Index strategy:
       - Uniqueness per conversation thread: (self_id, role, peer_id)
       - Fast scans for the send loop: (self_id, role)
       - Filtering and cleanup for nonce logs: (self_id, role, peer_id)
       - Fast nonce scan and dedupe-friendly: (self_id, role, peer_id, flow, nonce)

    DATA MODEL SUMMARY
      RoleState:
        state, local_nonce, peer_nonce, local_reference, peer_reference,
        exchange_count, finalize_retry_count, peer_address,
        (+ optional crypto columns if present: peer_sign_pub, peer_kx_pub, hs_derived_at, last_secure_at)
      NonceEvent:
        replay-friendly log; we use it for regular nonces and hs replay defense.
    """
    await RoleState.create_table(db)
    await NonceEvent.create_table(db)

    # RoleState
    await RoleState.create_index(db, "uq_role_peer", ["self_id", "role", "peer_id"], unique=True)
    await RoleState.create_index(db, "ix_role_scan", ["self_id", "role"], unique=False)

    # NonceEvent
    await NonceEvent.create_index(db, "ix_nonce_triplet", ["self_id", "role", "peer_id"], unique=False)
    await NonceEvent.create_index(db, "ix_nonce_exists", ["self_id", "role", "peer_id", "flow", "nonce"], unique=False)



""" ============================= CRYPTO HELPERS ============================ """
# ---[ CRYPTO ADDITIONS ]---
# Storage-agnostic adapter that makes NonceEvent usable for handshake replay/TTL.
class DBNonceStore:
    def __init__(self, *, self_id: str, role: str, peer_id: str, ttl_seconds: int = 60):
        self.self_id = self_id
        self.role = role
        self.peer_id = peer_id
        self.ttl_seconds = ttl_seconds

    async def exists(self, nonce: str) -> bool:
        rows = await NonceEvent.find(
            db,
            where={
                "self_id": self.self_id,
                "role": self.role,
                "peer_id": self.peer_id,
                "flow": "received",
                "nonce": nonce,
            }
        )
        return bool(rows)

    def is_expired(self, ts: _dt.datetime) -> bool:
        return (_dt.datetime.now() - ts).total_seconds() > self.ttl_seconds

    async def add(self, nonce: str, ts: _dt.datetime) -> None:
        # Mark this signed-handshake nonce as seen.
        await NonceEvent.insert(
            db,
            self_id=self.self_id,
            role=self.role,
            peer_id=self.peer_id,
            flow="received",
            nonce=nonce
        )

# ---[ CRYPTO ADDITIONS ]---
async def persist_crypto_meta(role: str, peer_id: str, **fields) -> None:
    """Best-effort persistence; safe to leave enabled even if columns don't exist yet."""
    if not PERSIST_CRYPTO:
        return
    try:
        await RoleState.update(
            db,
            where={"self_id": my_id, "role": role, "peer_id": peer_id},
            fields=fields
        )
    except Exception as e:
        # Column may not exist yet; keep silent to avoid breaking the demo.
        # Enable debug below if you want to see it:
        # client.logger.debug(f"[crypto:persist] skipped: {e}")
        pass
    
async def maybe_open_secure(role: str, peer_id: str, content: dict) -> None:
    """
    If a secure envelope is present and we have keys, verify + decrypt it
    and surface the plaintext at content['message'] for normal handling.

    Contract:
      - Requires SYM_KEYS[(role, peer_id)] and PEER_SIGN_PUB[(role, peer_id)].
      - Expects content["sec"] to be a sealed envelope created by seal_envelope(...).
      - On success, sets content["message"] to plaintext and records last_secure_at.
    """
    sec = content.get("sec")
    if not sec:
        return
    sym  = SYM_KEYS.get((role, peer_id))
    peer = PEER_SIGN_PUB.get((role, peer_id))
    if not (sym and peer):
        return
    try:
        obj = open_envelope(sym, peer, sec)
        if isinstance(obj, dict) and "message" in obj:
            content["message"] = obj["message"]
            await persist_crypto_meta(role, peer_id, last_secure_at=_dt.datetime.now(_dt.timezone.utc).isoformat())
            client.logger.info(f"[secure:{role}] opened message: {obj['message']!r}")
    except Exception as e:
        client.logger.warning(f"[secure:{role}] decrypt/verify failed: {e}")



""" ========================= CLIENT & FLOW SETUP =========================== """

client = SummonerClient(name=f"HSAgent_1")  # crypto variant

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")
client_flow.ready()

Trigger = client_flow.triggers()

# ==== Handshake phases (renamed for clarity) ====
# Initiator: init_ready -> init_exchange -> init_finalize_propose -> init_finalize_close -> init_ready
# Responder: resp_ready -> resp_confirm  -> resp_exchange         -> resp_finalize        -> resp_ready
#
# Roughly:
# - *_ready: idle/buffer state
# - *_exchange: alternating nonce ping-pong
# - *_finalize_propose / resp_finalize: exchange refs
# - init_finalize_close: initiator keeps sending "close" until responder acknowledges



""" ======================= ROLESTATE HELPERS (UTILS) ======================= """

async def ensure_role_state(self_id: str, role: str, peer_id: str, default_state: str) -> dict:
    """
    Make sure we have a RoleState row for (self_id, role, peer_id).
    If the 'state' is NULL, normalize it to default_state. Returns the row dict.

    Why:
      Receive handlers need consistent defaults to enforce nonce/ref invariants.
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
    Client calls this periodically. We return the allowed states for a peer derived from payload['from'].

    Behavior:
      - If payload does not identify a peer, return {} to avoid global leakage.
      - Otherwise, provide scoped keys:
          {"initiator:<peer>": <state>, "responder:<peer>": <state>}
    """
    peer_id = None
    if isinstance(payload, dict):
        peer_id = payload.get("from") or (payload.get("content", {}) or {}).get("from")

    if peer_id is None:
        # No peer: don't advertise global keys; client will retry with a peer.
        return {}

    # Peer-scoped advertisement, e.g. {"initiator:<peer>": "...", "responder:<peer>": "..."}
    i_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields=["state"])
    r_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields=["state"])

    i_state = i_rows[0]["state"] if i_rows and i_rows[0]["state"] else "init_ready"
    r_state = r_rows[0]["state"] if r_rows and r_rows[0]["state"] else "resp_ready"

    client.logger.info(f"\033[92m[upload] peer={peer_id[:5]} | initiator={i_state} | responder={r_state}\033[0m")
    return {f"initiator:{peer_id}": i_state, f"responder:{peer_id}": r_state}

@client.download_states()
async def download(possible_states: dict[Optional[str], list[Node]]) -> None:
    """
    Client tells us which states are currently permissible *per role*. We then set the RoleState.state
    to one of those. This applies to all peers of a role.

    Preference order:
      - Initiator: init_ready > init_finalize_close > init_finalize_propose > init_exchange
      - Responder: resp_ready > resp_finalize > resp_exchange > resp_confirm
    """
    ordered_states = {
        "initiator": ["init_ready", "init_finalize_close", "init_finalize_propose", "init_exchange"],
        "responder": ["resp_ready", "resp_finalize", "resp_exchange", "resp_confirm"],
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

        rows = await RoleState.find(db, where={"self_id": my_id, "role": role, "peer_id": peer_id})
        for row in rows:
            await RoleState.update(
                db,
                where={"self_id": my_id, "role": role, "peer_id": row["peer_id"]},
                fields={"state": target_state},
            )
        client.logger.info(f"[download] '{role}' set state -> '{target_state}' for {peer_id[:5]}")



""" =============================== HOOKS =================================== """

@client.hook(direction=Direction.RECEIVE)
async def validation(payload: Any) -> Optional[dict]:
    """
    Common receive hook: basic message shape and addressing checks.
    Returning the payload keeps the message; returning None drops it.

    Accepts only if:
      - payload has remote_addr and content
      - content has from, to, intent
      - to is None (broadcast) or equals my_id
      - from is not None
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
    Common send hook: attach our agent id as 'from' and log.
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
    HELLO stage (responder). Accept a fresh 'register' or a 'reconnect' with the initiator's remembered ref.

    Reconnect rule:
      Initiator must present our prior local_reference as your_ref. On acceptance we clear local_reference
      to allow a fresh finalize cycle.
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
    First request after confirm. We verify their 'your_nonce' matches our last 'local_nonce'.
    If present, we also validate the initiator's signed handshake blob ('hs') and derive a symmetric key.

    Handshake (init):
      - expected_type="init"
      - binds to the initiator's my_nonce
      - uses DBNonceStore for replay check and TTL window
      - successful validation sets SYM_KEYS[('responder', peer)] and PEER_SIGN_PUB
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

    # ---[ CRYPTO ADDITIONS ]---
    inserted_by_validator = False
    if "hs" in content:
        store = DBNonceStore(self_id=my_id, role="responder", peer_id=peer_id, ttl_seconds=60)
        try:
            sym = await validate_handshake_message(
                content["hs"], expected_type="init",
                expected_nonce=content["my_nonce"],
                nonce_store=store, priv_kx=kx_priv
            )
            SYM_KEYS[("responder", peer_id)] = sym
            PEER_SIGN_PUB[("responder", peer_id)] = content["hs"].get("sign_pub", "")
            client.logger.info(f"[resp_confirm -> resp_exchange] sym_key={sym[:8].hex()}...")
            await persist_crypto_meta(
                "responder", peer_id,
                peer_sign_pub=content["hs"].get("sign_pub"),
                peer_kx_pub=content["hs"].get("kx_pub"),
                hs_derived_at=_dt.datetime.now(_dt.timezone.utc).isoformat()
            )
            inserted_by_validator = True
        except Exception as e:
            client.logger.warning(f"[resp_confirm -> resp_exchange] handshake verify failed: {e}")

    await RoleState.update(
        db,
        where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
        fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "exchange_count": 1, "peer_address": addr}
    )
    if not inserted_by_validator:
        await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="received", nonce=content["my_nonce"])

    # Try to open a secure payload (if any)
    await maybe_open_secure("responder", peer_id, content)

    client.logger.info("[resp_confirm -> resp_exchange] FIRST REQUEST")
    return Move(Trigger.ok)

@client.receive(route="resp_exchange --> resp_finalize")
async def handle_request_or_conclude(payload: dict) -> Optional[Event]:
    """
    Either continue the exchange loop (new nonce) or accept the initiator's 'conclude' carrying their ref.

    Secure payloads:
      - At any point in exchange, if SYM_KEYS exists and 'sec' is present,
        we decrypt+verify and surface content['message'].
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
    
    # Try to open a secure payload (if any)
    await maybe_open_secure("responder", peer_id, content)

    if content["intent"] == "conclude":
        await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id},
            fields={
                "peer_reference": content["my_ref"], 
                "exchange_count": 0, 
                "peer_address": addr
            })
        client.logger.info("[resp_exchange -> resp_finalize] REQUEST TO CONCLUDE")
        return Move(Trigger.ok)

    # request (keep ping-pong going)
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
    Finalization (responder): after we send 'finish', we expect initiator's 'close'
    with both refs. On success, clean slate but keep references for reconnect on initiator.

    Failure path:
      - If finalize retries exceed FINAL_LIMIT, wipe refs and counters to avoid deadlock loops.
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
    if int(row.get("finalize_retry_count", 0)) > FINAL_LIMIT:
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
        return Move(Trigger.ok)

    new_retry = int(row.get("finalize_retry_count", 0)) + 1
    await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"finalize_retry_count": new_retry, "peer_address": addr})
    return Stay(Trigger.error)



""" ===================== RECEIVE HANDLERS — INITIATOR ====================== """

@client.receive(route="init_ready --> init_exchange")
async def handle_confirm(payload: dict) -> Optional[Event]:
    """
    HELLO stage (initiator): we receive the responder's 'confirm' and capture their first nonce.
    If present, we also validate the responder's signed handshake blob ('hs') and derive a symmetric key.

    Handshake (response):
      - expected_type="response"
      - binds to responder's my_nonce
      - successful validation stores SYM_KEYS[('initiator', peer)] and PEER_SIGN_PUB
    """
    addr = payload["remote_addr"]
    content = payload["content"]
    peer_id = content["from"]

    if not(content["intent"] == "confirm" and content["to"] is not None): return
    client.logger.info("[init_ready -> init_exchange] intent OK")

    await ensure_role_state(my_id, "initiator", peer_id, "init_ready")

    inserted_by_validator = False
    # ---[ CRYPTO ADDITIONS ]---
    # If responder attached a signed handshake blob, validate & derive sym key.
    if "my_nonce" in content and "hs" in content:
        store = DBNonceStore(self_id=my_id, role="initiator", peer_id=peer_id, ttl_seconds=60)
        try:
            sym = await validate_handshake_message(
                content["hs"], expected_type="response",
                expected_nonce=content["my_nonce"],
                nonce_store=store, priv_kx=kx_priv
            )
            SYM_KEYS[("initiator", peer_id)] = sym
            PEER_SIGN_PUB[("initiator", peer_id)] = content["hs"].get("sign_pub", "")
            client.logger.info(f"[init_ready -> init_exchange] sym_key={sym[:8].hex()}...")
            await persist_crypto_meta(
                "initiator", peer_id,
                peer_sign_pub=content["hs"].get("sign_pub"),
                peer_kx_pub=content["hs"].get("kx_pub"),
                hs_derived_at=_dt.datetime.now(_dt.timezone.utc).isoformat()
            )
            inserted_by_validator = True
        except Exception as e:
            client.logger.warning(f"[init_ready -> init_exchange] handshake verify failed: {e}")

    if "my_nonce" in content:
        await RoleState.update(
            db,
            where={"self_id": my_id, "role": "initiator", "peer_id": peer_id},
            fields={"peer_nonce": content["my_nonce"], "peer_address": addr}
        )
        if not inserted_by_validator:
            await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])

        # Try to open a secure payload (if any). (Unusual on confirm, but harmless.)
        await maybe_open_secure("initiator", peer_id, content)

        client.logger.info(f"[init_ready -> init_exchange] peer_nonce set: {content['my_nonce']}")
        return Move(Trigger.ok)

@client.receive(route="init_exchange --> init_finalize_propose")
async def handle_respond(payload: dict) -> Optional[Event]:
    """
    Exchange stage (initiator): we expect 'respond' where your_nonce == our last local_nonce.
    When exchange_count exceeds EXCHANGE_LIMIT, we cut to finalize.

    Secure payloads:
      - If sec present and we have a SYM key, message is surfaced as plaintext at content['message'].
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

    # Try to open a secure payload (if any)
    await maybe_open_secure("initiator", peer_id, content)
    
    if int(row.get("exchange_count", 0)) > EXCHANGE_LIMIT:
        # CUT: accept their nonce but reset the counter, then progress to finalize
        await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "exchange_count": 0, "peer_address": addr})
        await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
        client.logger.info(f"[init_exchange -> init_finalize_propose] EXCHANGE CUT (limit reached)")
        return Move(Trigger.ok)

    # Normal exchange: store their nonce and clear ours (we'll generate a new one when we send)
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_nonce": content["my_nonce"], "local_nonce": None, "peer_address": addr})
    await NonceEvent.insert(db, self_id=my_id, role="initiator", peer_id=peer_id, flow="received", nonce=content["my_nonce"])
    client.logger.info(f"[init_exchange -> init_finalize_propose] RESPOND")
    return Stay(Trigger.ok)

@client.receive(route="init_finalize_propose --> init_finalize_close")
async def handle_finish(payload: dict) -> Optional[Event]:
    """
    Finalize (initiator): after we send 'conclude' with our ref, we expect 'finish' carrying your_ref == our local_reference.

    Success path:
      - Record responder's my_ref as peer_reference
      - Clear NonceEvent log (transient exchange is done)
      - Proceed to close loop
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
    
    if int(row.get("finalize_retry_count", 0)) > FINAL_LIMIT:
        # CUT back to init_ready but *keep* references so reconnect can happen.
        await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"finalize_retry_count": 0, "peer_address": addr})
        client.logger.info("[init_finalize_propose -> init_finalize_close] CUT (finalize retry limit)")
        return Move(Trigger.ok)

    # Success: we now know the responder's ref; clear the transient nonce log.
    await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"peer_reference": content["my_ref"], "finalize_retry_count": 0, "peer_address": addr})
    await NonceEvent.delete(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id})
    client.logger.info("[init_finalize_propose -> init_finalize_close] CLOSE")
    return Move(Trigger.ok)

@client.receive(route="init_finalize_close --> init_ready")
async def finish_to_idle(payload: dict) -> Optional[Event]:
    """
    If we've exceeded FINAL_LIMIT, CUT back to ready but keep references so reconnect can happen.

    Rationale:
      Avoid indefinite close loops; keep refs so the initiator can present your_ref on reconnect.
    """
    content = payload["content"]
    peer_id = content["from"]

    if peer_id is None: return Stay(Trigger.ignore)

    row = await ensure_role_state(my_id, "initiator", peer_id, "init_ready")
    if int(row.get("finalize_retry_count", 0)) > FINAL_LIMIT:
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



""" ============================ SEND DRIVER ================================ """

@client.send(route="sending", multi=True)
async def trying() -> list[dict]:
    """
    This is the driver that periodically emits outbound messages *per peer* and *per role*
    based on the current RoleState.

    Crypto behavior:
      - Initiator: attaches "init" hs on the first request (new_cnt == 1).
      - Responder: attaches "response" hs on confirm.
      - If SYM_KEYS exists for (role, peer), plaintext "message" is moved into a sealed "sec".
    """
    client.logger.info("[send tick]")
    await asyncio.sleep(1)
    payloads = []

    # iterate all known peers for both roles (multi-peer)
    init_rows = await RoleState.find(db, where={"self_id": my_id, "role": "initiator"})
    resp_rows = await RoleState.find(db, where={"self_id": my_id, "role": "responder"})

    # Initiator role sends
    for row in init_rows:
        role_state = row.get("state") or "init_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "init_ready":
            if peer_id and row.get("peer_reference"):
            # reconnect if refs exist
                client.logger.info(f"[send][initiator:{role_state}] reconnect with {peer_id} under {row.get('peer_reference')}")
                payload = {"to": peer_id, "your_ref": row.get("peer_reference"), "intent": "reconnect"}

        elif role_state == "init_exchange":
            # guard: need peer_nonce to populate your_nonce
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before first request")
                continue
            # bump the exchange counter and emit a new nonce
            new_cnt = int(row.get("exchange_count", 0)) + 1
            local_nonce = row.get("local_nonce") or generate_nonce()
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
            # Attach an "init" signed handshake only on our first request in the cycle (new_cnt == 1).
            if new_cnt == 1:
                payload["hs"] = build_handshake_message("init", local_nonce, kx_priv, sign_priv)

            # ---[ CRYPTO ADDITIONS ]---
            # If we already have a symmetric key for this peer, seal the message.
            sym = SYM_KEYS.get(("initiator", peer_id))
            if sym and "message" in payload:
                msg_obj = {"message": payload.pop("message")}
                payload["sec"] = seal_envelope(sym, sign_priv, msg_obj)

        elif role_state == "init_finalize_propose":
            # guard: need peer_nonce for your_nonce in conclude
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for peer_nonce before conclude")
                continue
            # propose our reference and keep retry count for the close step
            new_retry = int(row.get("finalize_retry_count", 0)) + 1
            local_ref = row.get("local_reference") or generate_reference()
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"local_reference": local_ref, "finalize_retry_count": new_retry})
            client.logger.info(f"[send][initiator:{role_state}] conclude #{new_retry} | my_ref={local_ref}")
            payload = {
                "to": peer_id,
                "intent": "conclude",
                "your_nonce": row.get("peer_nonce"),
                "my_ref": local_ref,
            }

        elif role_state == "init_finalize_close":
            # guard: need both refs before close
            if row.get("peer_reference") is None or row.get("local_reference") is None:
                client.logger.info(f"[send][initiator:{role_state}] waiting for refs before close")
                continue
            # keep sending 'close' until acknowledged by the responder
            new_retry = int(row.get("finalize_retry_count", 0)) + 1
            await RoleState.update(db, where={"self_id": my_id, "role": "initiator", "peer_id": peer_id}, fields={"finalize_retry_count": new_retry})
            client.logger.info(f"[send][initiator:{role_state}] finish #{new_retry} | your_ref={row.get('peer_reference')}")
            payload = {
                "to": peer_id,
                "intent": "close",
                "your_ref": row.get("peer_reference"),
                "my_ref": row.get("local_reference"),
            }

        if payload is not None:
            payloads.append(payload)

    # Responder role sends
    for row in resp_rows:
        role_state = row.get("state") or "resp_ready"
        peer_id    = row["peer_id"]
        payload = None
        if role_state == "resp_confirm":
            # send our confirm with a nonce the initiator must echo as 'your_nonce'
            local_nonce = row.get("local_nonce") or generate_nonce()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            await NonceEvent.insert(db, self_id=my_id, role="responder", peer_id=peer_id, flow="sent", nonce=local_nonce)
            client.logger.info(f"[send][responder:{role_state}] confirm | my_nonce={local_nonce}")
            payload = {"to": peer_id, "intent": "confirm", "my_nonce": local_nonce}
            # Attach a "response" signed handshake on our confirm.
            payload["hs"] = build_handshake_message("response", local_nonce, kx_priv, sign_priv)

        elif role_state == "resp_exchange":
            # guard: need peer_nonce to populate your_nonce
            if row.get("peer_nonce") is None:
                client.logger.info(f"[send][responder:{role_state}] waiting for peer_nonce before respond")
                continue
            # respond with a fresh nonce each round
            local_nonce = row.get("local_nonce") or generate_nonce()
            await RoleState.update(db, where={"self_id": my_id, "role": "responder", "peer_id": peer_id}, fields={"local_nonce": local_nonce})
            client.logger.info(f"[send][responder:{role_state}] respond #{row.get('exchange_count', 0)} | my_nonce={local_nonce}")
            payload = {
                "to": peer_id,
                "intent": "respond",
                "your_nonce": row.get("peer_nonce"),
                "my_nonce": local_nonce,
                "message": "I am OK!"
            }

            # ---[ CRYPTO ADDITIONS ]---
            sym = SYM_KEYS.get(("responder", peer_id))
            if sym and "message" in payload:
                msg_obj = {"message": payload.pop("message")}
                payload["sec"] = seal_envelope(sym, sign_priv, msg_obj)

        elif role_state == "resp_finalize":
            # guard: need peer_reference to populate your_ref
            if row.get("peer_reference") is None:
                client.logger.info(f"[send][responder:{role_state}] waiting for peer_reference before finish")
                continue
            # provide our reference; initiator will later 'close'
            local_ref = row.get("local_reference") or generate_reference()
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

    # Broadcast a registration each tick so new peers can discover us.
    payloads.append({"to": None, "intent": "register"})
    return payloads



""" =============================== ENTRYPOINT ============================== """

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='Relative path to the client config JSON (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()
    config_path = args.config_path or "configs/client_config.json"

    # [NEW] This is the new, robust startup sequence.
    async def bootstrap():
        print("======================================================")
        print("  HSAgent-2 BOOTSTRAP AND SELF-TEST SEQUENCE")
        print("======================================================")
        
        # 1. Load config to get the base URL for the substrate.
        with open(config_path, 'r') as f: config = json.load(f)
        base_url = config.get("api", {}).get("base_url")
        if not base_url:
            raise RuntimeError(f"api.base_url not found in {config_path}")

        # 2. Provision a new, random, single-use user for the test.
        #    This ensures our test runs in a clean, isolated environment.
        test_creds = {
            "username": f"selftest-user-{uuid.uuid4().hex[:8]}",
            "password": secrets.token_hex(16)
        }
        print(f"[Bootstrap] Provisioning temporary test user: {test_creds['username']}")
        
        # We need a temporary API client just to register our test user.
        temp_api_client = SummonerAPIClient(base_url)
        try:
            # The login method will auto-register the user if they don't exist.
            await temp_api_client.login(test_creds)
            print("[Bootstrap] Temporary user provisioned successfully.")
        finally:
            await temp_api_client.close()


        # 3. RUN THE SELF-TESTS for our adapters using the temporary credentials.
        #    This is the "pre-flight check." If this fails, the agent will not start.
        await runSelfTests(base_url, test_creds)
        
        print("\n[Bootstrap] Self-tests passed. Proceeding with agent startup.")
        print("======================================================")

    # Run the bootstrap and self-test sequence first.
    # If it fails, an exception will be thrown and the script will exit.
    asyncio.run(bootstrap())

    # If the self-tests passed, we can now safely start the main agent.
    # The `client.run()` call will perform its own API login using the
    # credentials specified in the config file.
    try:
        # In a real migration, the old `setup()` call would be removed.
        # client.loop.run_until_complete(setup())
        
        # The main, blocking call to start the agent's primary mission.
        client.run(
            host="127.0.0.1",
            port=8888,
            config_path=config_path
        )
    finally:
        # In a real migration, this would be removed.
        # asyncio.run(db.close())
        print("Agent shutdown complete.")

