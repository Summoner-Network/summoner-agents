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
from typing import Any, Callable, Optional
from pathlib import Path

import argparse

# ---[ CRYPTO ADDITIONS ]---
# Per-agent KX + signing keys; handshake helpers.
import datetime as _dt
import secrets
from cryptography.hazmat.primitives.asymmetric import x25519, ed25519
from api import ElmType, HybridNonceStore, SubstrateStateStore
from adapt import APIError, SummonerAPIClient
from selftest import runSelfTests
from crypto_utils import (
    decrypt_identity_from_vault_attrs, seal_envelope, open_envelope,
    build_handshake_message, serialize_public_key,
    validate_handshake_message,
    load_identity_json_encrypted, save_identity_json_encrypted,
    encrypt_identity_for_vault
)



""" ======================== CONSTANTS & SIMPLE HELPERS ===================== """
# Counters to simulate conversation with several exchanges.
# exchange = alternating request/response rounds before we cut to finalize
# finalize = # of "finish/close" attempts before cutting back to ready
EXCHANGE_LIMIT = 3
FINAL_LIMIT = 3
API_CLIENT: Optional[SummonerAPIClient] = None # This will be our single, authoritative instance
NONCE_STORE = None
STATE_STORE: Optional[SubstrateStateStore] = None
NONCE_STORE_FACTORY: Optional[Callable[[str], HybridNonceStore]] = None

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

""" ============================= CRYPTO HELPERS ============================ """

# ---[ CRYPTO ADDITIONS - REFACTORED & HARDENED FOR SUBSTRATE ]---
async def persist_crypto_meta(role: str, peer_id: str, **fields) -> None:
    """
    [REVISED] Best-effort persistence of cryptographic metadata. This version
    includes hardened error handling to distinguish between recoverable race
    conditions (version clashes) and unexpected API failures.
    """
    if not PERSIST_CRYPTO or not STATE_STORE:
        return
    try:
        # The underlying call to the state store adapter remains the same,
        # as its public interface is stable.
        await STATE_STORE.update_role_state(role, peer_id, fields)

    except APIError as e:
        # A 500 error from our backend on a PUT operation signifies a version
        # clash due to a race condition. This is expected and safe to ignore,
        # as the state will sync on a subsequent interaction.
        if e.status_code == 500:
            client.logger.warning(
                f"[crypto:persist] Version clash for peer {peer_id}. "
                "This is a normal race condition and is safe to ignore."
            )
        else:
            # Other API errors are unexpected and should be logged as errors.
            client.logger.error(f"[crypto:persist] Unexpected API error for peer {peer_id}: {e}")
            
    except RuntimeError as e:
        # This can happen if another concurrent process deleted the state object
        # between the read and write steps. It's a race condition.
        client.logger.warning(f"[crypto:persist] Could not update state for peer {peer_id}, likely deleted: {e}")
    except Exception as e:
        # A final catch-all for anything truly unexpected.
        client.logger.error(f"[crypto:persist] A critical unexpected error occurred for peer {peer_id}: {e}", exc_info=True)

    
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

client = SummonerClient(name=f"HSAgent_2")  # crypto variant (cloud native)

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

# ---[ REFACTORED & HARDENED FOR SUBSTRATE ]---
async def ensure_role_state(self_id: str, role: str, peer_id: str, default_state: str) -> dict:
    """
    [REVISED] Finds or creates the HandshakeState object for a conversation.
    This version adds hardened error handling to log and propagate failures
    gracefully, preventing silent crashes in the agent's receiver loop.
    """
    if not STATE_STORE:
        # This is a fatal configuration error; the agent cannot run without the state store.
        raise RuntimeError("STATE_STORE has not been initialized. Cannot ensure role state.")

    try:
        # The core logic remains the same, relying on our robust state store adapter.
        state_obj, _ = await STATE_STORE.ensure_role_state(role, peer_id, default_state)

        # We return only the 'attrs' portion to maintain the function's contract
        # with the rest of the agent's logic.
        return state_obj["attrs"]

    except APIError as e:
        # Log the specific API error with context, then re-raise it so the
        # calling function can handle the failure.
        client.logger.error(f"[ensure_role_state] API error while ensuring state for role={role}, peer={peer_id}: {e}")
        raise

    except Exception as e:
        # Catch any other unexpected errors, log them as critical, and re-raise.
        client.logger.critical(
            f"[ensure_role_state] CRITICAL unexpected error for role={role}, peer={peer_id}: {e}",
            exc_info=True
        )
        raise

""" ============== STATE ADVERTISING (UPLOAD/DOWNLOAD NEGOTIATION) ========= """

# ---[ REFACTORED & HARDENED FOR SUBSTRATE ]---
@client.upload_states()
async def upload(payload: dict) -> dict[str, str]:
    """
    [REVISED] Reports agent state to the server. This version is hardened to
    work with the split-state model, translates peer UUIDs to object IDs, and
    handles API errors gracefully.
    """
    if not STATE_STORE:
        return {}

    peer_agent_uuid = None
    if isinstance(payload, dict):
        peer_agent_uuid = payload.get("from") or (payload.get("content", {}) or {}).get("from")

    if not peer_agent_uuid:
        return {}

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        # This is essential as all state is keyed by the object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            # If we can't find the peer in the substrate, we can't report its state.
            return {}

        # Step 2: Fetch the split-state objects using the correct internal object ID.
        i_send_obj, _ = await STATE_STORE._find_state_objects("initiator", peer_identity_obj_id)
        r_send_obj, _ = await STATE_STORE._find_state_objects("responder", peer_identity_obj_id)

        # Step 3: Extract the 'state' attribute, which lives exclusively in the "send" object.
        # Use a default state if the corresponding object hasn't been created yet.
        i_state = i_send_obj["attrs"]["state"] if i_send_obj else "init_ready"
        r_state = r_send_obj["attrs"]["state"] if r_send_obj else "resp_ready"

        client.logger.info(f"\033[92m[upload] peer={peer_agent_uuid[:5]} | initiator={i_state} | responder={r_state}\033[0m")
        
        # The key for the returned dictionary MUST be the internal object ID for the
        # server's state negotiation logic to work correctly.
        return {f"initiator:{peer_identity_obj_id}": i_state, f"responder:{peer_identity_obj_id}": r_state}

    except Exception as e:
        # On any failure (API error, etc.), log it and return an empty dict
        # to prevent the agent's core loop from crashing.
        client.logger.error(f"[upload] Failed to get state for peer {peer_agent_uuid[:5]}: {e}")
        return {}

# ---[ REFACTORED & HARDENED FOR SUBSTRATE ]---
@client.download_states()
async def download(possible_states: dict[Optional[str], list[Node]]) -> None:
    """
    [REVISED] Receives permissible states from the server and updates the
    agent's state in the BOSS substrate. This version includes hardened,
    contextual error logging.
    """
    if not STATE_STORE:
        return

    ordered_states = {
        "initiator": ["init_ready", "init_finalize_close", "init_finalize_propose", "init_exchange"],
        "responder": ["resp_ready", "resp_finalize", "resp_exchange", "resp_confirm"],
    }

    for key, role_states in possible_states.items():
        if key is None or ":" not in str(key):
            continue

        # The peer_id here is the internal database object ID, not the public UUID.
        role, peer_id = key.split(":", 1)
        if role not in ordered_states or not peer_id:
            continue

        # Determine the best state to transition to based on the server's suggestions.
        target_state = next((s for s in ordered_states[role] if Node(s) in role_states), None)
        if not target_state:
            continue

        try:
            # This is a resilient, atomic update to the 'state' field.
            await STATE_STORE.update_role_state(role, peer_id, {"state": target_state})
            client.logger.info(f"[download] '{role}' set state -> '{target_state}' for peer {peer_id[:5]}")
        
        except APIError as e:
            # Gracefully handle the inevitable version clash as a non-fatal warning.
            if e.status_code == 500:
                client.logger.warning(
                    f"[download] Version clash for peer {peer_id} while setting state to {target_state}. "
                    "This is a normal race condition. State will sync on next tick."
                )
            else:
                # Log other, more serious API errors.
                client.logger.error(f"[download] API error for peer {peer_id}: {e}")
        
        except RuntimeError as e:
            # This can happen if the state was deleted by a concurrent process.
            client.logger.warning(f"[download] Could not update state for peer {peer_id}: {e}")
            
        except Exception as e:
            # A final catch-all for anything truly unexpected.
            client.logger.critical(f"[download] CRITICAL unexpected error for peer {peer_id}: {e}", exc_info=True)

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
      - ✅ [THE FIX] from is NOT my_id (ignore our own broadcasts)
    """
    if isinstance(payload, str) and payload.startswith("Warning:"):
        client.logger.warning(f"[server] {payload}")
    if not("remote_addr" in payload and "content" in payload): return
    content = payload["content"]
    if not("from" in content and "to" in content and "intent" in content): return
    if content["to"] is not None and content["to"] != my_id: return
    if content["from"] is None: return

    # ✅ THIS IS THE FIX: Add this line to reject self-sent messages.
    if content["from"] == my_id: return None

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
    [REVISED & HARDENED] HELLO stage (responder). This version adds a
    proactive state update to break the state negotiation deadlock.
    """
    if not STATE_STORE or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["intent"] in ["register", "reconnect"]):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]
    addr = payload["remote_addr"]

    if content["intent"] == "register" and my_id <= peer_agent_uuid:
        client.logger.info(f"[handle_register] Ignoring register from peer {peer_agent_uuid[:5]} due to symmetry rule. I will initiate.")
        return Stay(Trigger.ignore)

    try:
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_register] Received register from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        state_obj, created = await STATE_STORE.ensure_role_state("responder", peer_identity_obj_id, "resp_ready")
        if not created:
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {"peer_address": addr})
        
        state_attrs = state_obj["attrs"]

        # ✅ THE FIX: Proactively write the next state to the database to win the race condition.
        if content["intent"] == "register" and content["to"] is None and state_attrs.get("local_reference") is None:
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {"state": "resp_confirm"})
            client.logger.info(f"[resp_ready -> resp_confirm] REGISTER | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)

        if content["intent"] == "reconnect" and content.get("your_ref") == state_attrs.get("local_reference"):
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {"state": "resp_confirm"})
            client.logger.info(f"[resp_ready -> resp_confirm] RECONNECT | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)

    except APIError as e:
        client.logger.error(f"[handle_register] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_register] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)
        
    return Stay(Trigger.ignore)



@client.receive(route="resp_confirm --> resp_exchange")
async def handle_request(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the first request from the initiator,
    validates the handshake, derives session keys, and updates state. This
    version correctly uses internal object IDs for all state management and
    includes robust, multi-level error handling.
    """
    if not STATE_STORE or not NONCE_STORE_FACTORY or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["intent"] == "request" and content["to"] is not None):
        return Stay(Trigger.ignore)
    if not("your_nonce" in content and "my_nonce" in content):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_request] Received request from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Ensure we have a state object for this peer and validate the nonce.
        state_attrs = await ensure_role_state(my_id, "responder", peer_identity_obj_id, "resp_ready")
        if state_attrs.get("local_nonce") != content["your_nonce"]:
            client.logger.warning(f"[handle_request] Nonce mismatch for peer {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 3: Handle the cryptographic handshake if present.
        if "hs" in content:
            nonce_store = NONCE_STORE_FACTORY(peer_identity_obj_id)
            try:
                sym = await validate_handshake_message(
                    content["hs"], expected_type="init",
                    expected_nonce=content["my_nonce"],
                    nonce_store=nonce_store, priv_kx=kx_priv
                )
                # Use the internal object ID as the key for all in-memory and persistent state.
                SYM_KEYS[("responder", peer_identity_obj_id)] = sym
                PEER_SIGN_PUB[("responder", peer_identity_obj_id)] = content["hs"].get("sign_pub", "")
                
                await persist_crypto_meta(
                    "responder", peer_identity_obj_id,
                    peer_sign_pub=content["hs"].get("sign_pub"),
                    peer_kx_pub=content["hs"].get("kx_pub"),
                    hs_derived_at=_dt.datetime.now(_dt.timezone.utc).isoformat()
                )
            except ValueError as e:
                # A ValueError from validation is a security warning (e.g., bad signature, replay).
                client.logger.warning(f"[handle_request] Handshake validation failed for peer {peer_agent_uuid[:5]}: {e}")
                return Stay(Trigger.ignore) # Abort the transition.

        # Step 4: Update the protocol state in the substrate.
        await STATE_STORE.update_role_state(
            "responder",
            peer_identity_obj_id,
            {
                "peer_nonce": content["my_nonce"],
                "local_nonce": None,
                "exchange_count": 1,
                "peer_address": payload["remote_addr"]
            }
        )

        await maybe_open_secure("responder", peer_identity_obj_id, content)

        client.logger.info(f"[resp_confirm -> resp_exchange] FIRST REQUEST | peer_obj_id={peer_identity_obj_id}")
        return Move(Trigger.ok)

    except APIError as e:
        client.logger.error(f"[handle_request] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_request] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)


@client.receive(route="resp_exchange --> resp_finalize")
async def handle_request_or_conclude(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the core exchange loop or a request to conclude.
    This version correctly uses internal object IDs for all state management and
    includes robust error handling.
    """
    if not STATE_STORE or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    # Basic payload shape validation.
    if not(content["intent"] in ["request", "conclude"] and content["to"] is not None):
        return Stay(Trigger.ignore)
    if not("your_nonce" in content and (("my_nonce" in content and content["intent"] == "request") or
                                        ("my_ref" in content and content["intent"] == "conclude"))):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_req_or_conclude] Received message from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Ensure we have a state object for this peer and validate the nonce.
        state_attrs = await ensure_role_state(my_id, "responder", peer_identity_obj_id, "resp_ready")
        if state_attrs.get("local_nonce") != content["your_nonce"]:
            client.logger.warning(f"[handle_req_or_conclude] Nonce mismatch for peer {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)
        
        await maybe_open_secure("responder", peer_identity_obj_id, content)

        # Step 3: Branch logic based on the message intent.
        if content["intent"] == "conclude":
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {
                "peer_reference": content["my_ref"], 
                "exchange_count": 0, 
                "peer_address": payload["remote_addr"]
            })
            client.logger.info(f"[resp_exchange -> resp_finalize] CONCLUDE | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)

        # This is the "request" path (continue the exchange).
        new_count = int(state_attrs.get("exchange_count", 0)) + 1
        await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {
            "peer_nonce": content["my_nonce"], 
            "local_nonce": None, 
            "exchange_count": new_count, 
            "peer_address": payload["remote_addr"]
        })
        
        client.logger.info(f"[resp_exchange -> resp_exchange] REQUEST #{new_count} | peer_obj_id={peer_identity_obj_id}")
        return Stay(Trigger.ok) # Stay in the exchange state for the next round.

    except APIError as e:
        client.logger.error(f"[handle_req_or_conclude] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_req_or_conclude] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)


@client.receive(route="resp_finalize --> resp_ready")
async def handle_close(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the final "close" message from the initiator or
    times out the finalization process. This version correctly uses internal
    object IDs and includes robust error handling for all exit paths.
    """
    if not STATE_STORE or not NONCE_STORE_FACTORY or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["to"] is not None):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_close] Received message from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Fetch the current state for this peer.
        state_attrs = await ensure_role_state(my_id, "responder", peer_identity_obj_id, "resp_ready")

        # --- Success Path: We received a valid "close" message ---
        if content["intent"] == "close":
            if not("your_ref" in content and "my_ref" in content):
                return Stay(Trigger.ignore)
            
            if state_attrs.get("local_reference") != content["your_ref"]:
                client.logger.warning(f"[handle_close] Reference mismatch for peer {peer_agent_uuid[:5]}. Ignoring.")
                return Stay(Trigger.ignore)

            # Reset the state object in the substrate.
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {
                "peer_reference": content["my_ref"], "local_nonce": None,
                "peer_nonce": None, "finalize_retry_count": 0, "exchange_count": 0,
                "peer_address": payload["remote_addr"]
            })

            # Clean up the cryptographic nonce journal.
            nonce_store = NONCE_STORE_FACTORY(peer_identity_obj_id)
            await nonce_store.delete_journal()

            client.logger.info(f"[resp_finalize -> resp_ready] CLOSE SUCCESS | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)
        
        # --- Timeout Path: The initiator failed to send "close" in time ---
        if int(state_attrs.get("finalize_retry_count", 0)) > FINAL_LIMIT:
            client.logger.warning(f"[resp_finalize -> resp_ready] FINALIZE RETRY LIMIT for peer {peer_agent_uuid[:5]}. Resetting.")
            await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {
                "local_nonce": None, "peer_nonce": None, "local_reference": None,
                "peer_reference": None, "exchange_count": 0, "finalize_retry_count": 0,
                "peer_address": payload["remote_addr"]
            })
            return Move(Trigger.ok)

        # --- Retry Path: Increment the counter and wait for "close" again ---
        new_retry = int(state_attrs.get("finalize_retry_count", 0)) + 1
        await STATE_STORE.update_role_state("responder", peer_identity_obj_id, {"finalize_retry_count": new_retry})
        client.logger.info(f"[resp_finalize] Still waiting for close from peer {peer_agent_uuid[:5]} (attempt {new_retry})")
        return Stay(Trigger.error) # Signal to the FSM that we are in a waiting/retry state.

    except APIError as e:
        client.logger.error(f"[handle_close] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_close] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)


""" ===================== RECEIVE HANDLERS — INITIATOR ====================== """


@client.receive(route="init_ready --> init_exchange")
async def handle_confirm(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the "confirm" message from the responder,
    validates the handshake, and establishes the secure session. This version
    correctly uses internal object IDs and includes robust error handling.
    """
    if not STATE_STORE or not NONCE_STORE_FACTORY or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["intent"] == "confirm" and content["to"] is not None):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_confirm] Received confirm from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Ensure we have a state object for this peer.
        await ensure_role_state(my_id, "initiator", peer_identity_obj_id, "init_ready")

        # Step 3: Handle the cryptographic handshake if present.
        if "my_nonce" in content and "hs" in content:
            nonce_store = NONCE_STORE_FACTORY(peer_identity_obj_id)
            try:
                sym = await validate_handshake_message(
                    content["hs"], expected_type="response",
                    expected_nonce=content["my_nonce"],
                    nonce_store=nonce_store, priv_kx=kx_priv
                )
                # Use the internal object ID as the key for all state.
                SYM_KEYS[("initiator", peer_identity_obj_id)] = sym
                PEER_SIGN_PUB[("initiator", peer_identity_obj_id)] = content["hs"].get("sign_pub", "")
                
                await persist_crypto_meta(
                    "initiator", peer_identity_obj_id,
                    peer_sign_pub=content["hs"].get("sign_pub"),
                    peer_kx_pub=content["hs"].get("kx_pub"),
                    hs_derived_at=_dt.datetime.now(_dt.timezone.utc).isoformat()
                )
            except ValueError as e:
                # A validation failure is a security warning, not a system crash.
                client.logger.warning(f"[handle_confirm] Handshake validation failed for peer {peer_agent_uuid[:5]}: {e}")
                return Stay(Trigger.ignore)

        # Step 4: Update the protocol state in the substrate.
        if "my_nonce" in content:
            await STATE_STORE.update_role_state(
                "initiator", peer_identity_obj_id,
                {"peer_nonce": content["my_nonce"], "peer_address": payload["remote_addr"]}
            )
            
            await maybe_open_secure("initiator", peer_identity_obj_id, content)

            client.logger.info(f"[init_ready -> init_exchange] CONFIRMED | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)

    except APIError as e:
        client.logger.error(f"[handle_confirm] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_confirm] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)
        
    return Stay(Trigger.ignore)


@client.receive(route="init_exchange --> init_finalize_propose")
async def handle_respond(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the core initiator exchange loop. This version
    correctly uses internal object IDs for all state management and includes
    robust error handling.
    """
    if not STATE_STORE or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["intent"] == "respond" and content["to"] is not None):
        return Stay(Trigger.ignore)
    if not("your_nonce" in content and "my_nonce" in content):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_respond] Received message from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Ensure we have a state object for this peer and validate the nonce.
        state_attrs = await ensure_role_state(my_id, "initiator", peer_identity_obj_id, "init_ready")
        if state_attrs.get("local_nonce") != content["your_nonce"]:
            client.logger.warning(f"[handle_respond] Nonce mismatch for peer {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        await maybe_open_secure("initiator", peer_identity_obj_id, content)
        
        # Step 3: Branch logic for continuing the exchange or cutting to finalization.
        if int(state_attrs.get("exchange_count", 0)) > EXCHANGE_LIMIT:
            # "CUT" branch: The exchange limit has been reached.
            await STATE_STORE.update_role_state("initiator", peer_identity_obj_id, {
                "peer_nonce": content["my_nonce"],
                "exchange_count": 0,
                "peer_address": payload["remote_addr"]
            })
            client.logger.info(f"[init_exchange -> init_finalize_propose] EXCHANGE CUT | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)

        # "Normal Exchange" branch: Continue the ping-pong.
        await STATE_STORE.update_role_state("initiator", peer_identity_obj_id, {
            "peer_nonce": content["my_nonce"],
            "local_nonce": None,  # Clear our nonce; the send driver will generate a new one.
            "peer_address": payload["remote_addr"]
        })
        client.logger.info(f"[init_exchange -> init_exchange] RESPOND | peer_obj_id={peer_identity_obj_id}")
        return Stay(Trigger.ok) # Stay in the exchange state for the next round.

    except APIError as e:
        client.logger.error(f"[handle_respond] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_respond] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)


@client.receive(route="init_finalize_propose --> init_finalize_close")
async def handle_finish(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] Handles the "finish" message from the responder. This
    version correctly uses internal object IDs and includes robust error handling.
    """
    if not STATE_STORE or not NONCE_STORE_FACTORY or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    if not(content["intent"] == "finish" and content["to"] is not None):
        return Stay(Trigger.ignore)
    if not("your_ref" in content and "my_ref" in content):
        return Stay(Trigger.ignore)

    peer_agent_uuid = content["from"]

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            client.logger.warning(f"[handle_finish] Received message from unknown peer UUID {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)

        # Step 2: Fetch the current state and validate the reference.
        state_attrs = await ensure_role_state(my_id, "initiator", peer_identity_obj_id, "init_ready")
        if state_attrs.get("local_reference") != content["your_ref"]:
            client.logger.warning(f"[handle_finish] Reference mismatch for peer {peer_agent_uuid[:5]}. Ignoring.")
            return Stay(Trigger.ignore)
        
        # This is the "Success" path.
        await STATE_STORE.update_role_state("initiator", peer_identity_obj_id, {
            "peer_reference": content["my_ref"],
            "finalize_retry_count": 0,
            "peer_address": payload["remote_addr"]
        })
        
        # Clean up the cryptographic nonce journal for this session.
        nonce_store = NONCE_STORE_FACTORY(peer_identity_obj_id)
        await nonce_store.delete_journal()
        
        client.logger.info(f"[init_finalize_propose -> init_finalize_close] CLOSE | peer_obj_id={peer_identity_obj_id}")
        return Move(Trigger.ok)

    except APIError as e:
        client.logger.error(f"[handle_finish] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[handle_finish] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)


@client.receive(route="init_finalize_close --> init_ready")
async def finish_to_idle(payload: dict) -> Optional[Event]:
    """
    [REVISED & HARDENED] The final safety valve for the initiator. This is a
    timeout handler that performs a full state reset to prevent infinite
    reconnect loops.
    """
    if not STATE_STORE or not my_id:
        return Stay(Trigger.ignore)

    content = payload["content"]
    peer_agent_uuid = content.get("from")

    if peer_agent_uuid is None:
        return Stay(Trigger.ignore)

    try:
        # Step 1: Translate the public peer UUID to its internal database object ID.
        peer_identity_obj_id = await STATE_STORE._find_peer_identity_id(peer_agent_uuid)
        if not peer_identity_obj_id:
            # If we don't know the peer, there's no state to clean up.
            return Stay(Trigger.ignore)

        # Step 2: Fetch the current state to check the retry counter.
        state_attrs = await ensure_role_state(my_id, "initiator", peer_identity_obj_id, "init_ready")
        
        if int(state_attrs.get("finalize_retry_count", 0)) > FINAL_LIMIT:
            # The finalization has timed out. Perform a full state reset.
            await STATE_STORE.update_role_state("initiator", peer_identity_obj_id, {
                "local_nonce": None, "peer_nonce": None, "local_reference": None,
                "peer_reference": None, "exchange_count": 0, "finalize_retry_count": 0
            })
            client.logger.info(f"[init_finalize_close -> init_ready] CUT (timeout) | peer_obj_id={peer_identity_obj_id}")
            return Move(Trigger.ok)
            
    except APIError as e:
        client.logger.error(f"[finish_to_idle] API error for peer {peer_agent_uuid[:5]}: {e}")
        return Stay(Trigger.error)
    except Exception as e:
        client.logger.critical(f"[finish_to_idle] CRITICAL error for peer {peer_agent_uuid[:5]}: {e}", exc_info=True)
        return Stay(Trigger.error)
        
    # If the retry limit is not yet reached, we don't move state.
    return Stay(Trigger.ignore)



""" ============================ SEND DRIVER ================================ """

# ---[ REFACTORED FOR SUBSTRATE ]---
@client.send(route="sending", multi=True)
async def trying() -> list[dict]:
    """
    [REVISED & HARDENED] The periodic send driver. This final version correctly
    distinguishes between internal object IDs (for state) and public UUIDs (for
    messaging), is efficient, and handles per-peer errors gracefully.
    """
    if not STATE_STORE or not API_CLIENT or not my_id:
        return []

    await asyncio.sleep(1)
    payloads = []

    try:
        # Step 1: Discover all active conversations, fetching the full state objects once.
        init_state_objects = await STATE_STORE.find_role_states("initiator")
        resp_state_objects = await STATE_STORE.find_role_states("responder")
    except Exception as e:
        client.logger.error(f"[Send Driver] Failed to discover peer states: {e}")
        return [] # Abort tick if we can't read initial state

    # --- Initiator Role Logic ---
    for state_obj in init_state_objects:
        row = state_obj["attrs"]
        peer_obj_id = row.get("peerId")
        if not peer_obj_id:
            continue

        try:
            # Step 2: For each peer, get their public UUID for messaging.
            peer_identity = await API_CLIENT.boss.get_object(ElmType.AgentIdentity, peer_obj_id)
            peer_agent_uuid = peer_identity.get("attrs", {}).get("agentId")
            if not peer_agent_uuid or peer_agent_uuid == my_id:
                continue # Guard against self-interaction.

            # Step 3: Generate the correct payload based on the current state.
            role_state = row.get("state") or "init_ready"
            payload = None
            
            if role_state == "init_ready":
                if row.get("peer_reference"):
                    payload = {"to": peer_agent_uuid, "your_ref": row.get("peer_reference"), "intent": "reconnect"}

            elif role_state == "init_exchange":
                if row.get("peer_nonce") is None: continue
                new_cnt = int(row.get("exchange_count", 0)) + 1
                local_nonce = row.get("local_nonce") or generate_nonce()
                await STATE_STORE.update_role_state("initiator", peer_obj_id, {"local_nonce": local_nonce, "exchange_count": new_cnt})
                payload = {"to": peer_agent_uuid, "intent": "request", "your_nonce": row.get("peer_nonce"), "my_nonce": local_nonce, "message": "How are you?"}
                if new_cnt == 1 and kx_priv and sign_priv:
                    payload["hs"] = build_handshake_message("init", local_nonce, kx_priv, sign_priv)
                if (sym := SYM_KEYS.get(("initiator", peer_obj_id))) and sign_priv:
                    payload["sec"] = seal_envelope(sym, sign_priv, {"message": payload.pop("message")})

            elif role_state == "init_finalize_propose":
                if row.get("peer_nonce") is None: continue
                new_retry = int(row.get("finalize_retry_count", 0)) + 1
                local_ref = row.get("local_reference") or generate_reference()
                await STATE_STORE.update_role_state("initiator", peer_obj_id, {"local_reference": local_ref, "finalize_retry_count": new_retry})
                payload = {"to": peer_agent_uuid, "intent": "conclude", "your_nonce": row.get("peer_nonce"), "my_ref": local_ref}

            elif role_state == "init_finalize_close":
                if row.get("peer_reference") and row.get("local_reference"):
                    new_retry = int(row.get("finalize_retry_count", 0)) + 1
                    await STATE_STORE.update_role_state("initiator", peer_obj_id, {"finalize_retry_count": new_retry})
                    payload = {"to": peer_agent_uuid, "intent": "close", "your_ref": row.get("peer_reference"), "my_ref": row.get("local_reference")}

            if payload:
                payloads.append(payload)

        except APIError as e:
            if e.status_code == 500: client.logger.warning(f"[Send Driver] Version clash for initiator/peer {peer_obj_id}. Retrying next tick.")
            else: client.logger.error(f"[Send Driver] API Error for initiator/peer {peer_obj_id}: {e}")
        except Exception as e:
            client.logger.error(f"[Send Driver] Unexpected error for initiator/peer {peer_obj_id}: {e}", exc_info=True)
    
    # --- Responder Role Logic ---
    for state_obj in resp_state_objects:
        row = state_obj["attrs"]
        peer_obj_id = row.get("peerId")
        if not peer_obj_id:
            continue
            
        try:
            peer_identity = await API_CLIENT.boss.get_object(ElmType.AgentIdentity, peer_obj_id)
            peer_agent_uuid = peer_identity.get("attrs", {}).get("agentId")
            if not peer_agent_uuid or peer_agent_uuid == my_id:
                continue

            role_state = row.get("state") or "resp_ready"
            payload = None
            
            if role_state == "resp_confirm":
                local_nonce = row.get("local_nonce") or generate_nonce()
                await STATE_STORE.update_role_state("responder", peer_obj_id, {"local_nonce": local_nonce})
                payload = {"to": peer_agent_uuid, "intent": "confirm", "my_nonce": local_nonce}
                if kx_priv and sign_priv:
                    payload["hs"] = build_handshake_message("response", local_nonce, kx_priv, sign_priv)

            elif role_state == "resp_exchange":
                if row.get("peer_nonce") is None: continue
                local_nonce = row.get("local_nonce") or generate_nonce()
                await STATE_STORE.update_role_state("responder", peer_obj_id, {"local_nonce": local_nonce})
                payload = {"to": peer_agent_uuid, "intent": "respond", "your_nonce": row.get("peer_nonce"), "my_nonce": local_nonce, "message": "I am OK!"}
                if (sym := SYM_KEYS.get(("responder", peer_obj_id))) and sign_priv:
                    payload["sec"] = seal_envelope(sym, sign_priv, {"message": payload.pop("message")})

            elif role_state == "resp_finalize":
                if row.get("peer_reference") is None: continue
                local_ref = row.get("local_reference") or generate_reference()
                await STATE_STORE.update_role_state("responder", peer_obj_id, {"local_reference": local_ref})
                payload = {"to": peer_agent_uuid, "intent": "finish", "your_ref": row.get("peer_reference"), "my_ref": local_ref}

            if payload:
                payloads.append(payload)
        
        except APIError as e:
            if e.status_code == 500: client.logger.warning(f"[Send Driver] Version clash for responder/peer {peer_obj_id}. Retrying next tick.")
            else: client.logger.error(f"[Send Driver] API Error for responder/peer {peer_obj_id}: {e}")
        except Exception as e:
            client.logger.error(f"[Send Driver] Unexpected error for responder/peer {peer_obj_id}: {e}", exc_info=True)

    # Finally, add the broadcast registration message to discover new peers.
    payloads.append({"to": None, "intent": "register"})
    return payloads


""" =============================== ENTRYPOINT ============================== """

async def hydrate_agent(api_client: SummonerAPIClient) -> str:
    """
    [FINAL & CORRECTED] Performs the agent's hydration ritual. This version
    ensures all created associations conform to the server's validation schema.
    """
    global kx_priv, sign_priv, my_id
    client.logger.info(f"[Hydration] Discovering identity for agent '{id_args.name}' in substrate...")
    
    my_identity_obj = None
    try:
        assoc_response = await api_client.boss.get_associations(id_args.name, api_client.user_id, {"isDisplayName": "true"})
        if assoc_response and assoc_response.get("associations"):
            identity_id = assoc_response["associations"][0]["targetId"]
            my_identity_obj = await api_client.boss.get_object(ElmType.AgentIdentity, identity_id)
    except APIError as e:
        raise RuntimeError(f"API error during agent discovery: {e}")

    if not my_identity_obj:
        client.logger.warning(f"No identity found for agent '{id_args.name}'. Provisioning a new one...")
        
        agent_uuid = str(uuid.uuid4())
        local_kx_priv = x25519.X25519PrivateKey.generate()
        local_sign_priv = ed25519.Ed25519PrivateKey.generate()
        
        vault_attrs = encrypt_identity_for_vault(IDENT_PASSWORD, agent_uuid, local_kx_priv, local_sign_priv)
        identity_attrs = {
            "agentId": agent_uuid, "ownerId": api_client.user_id, "displayName": id_args.name,
            "signPubB64": serialize_public_key(local_sign_priv.public_key()),
            "kxPubB64": serialize_public_key(local_kx_priv.public_key()),
        }

        vault_res = await api_client.boss.put_object({"type": ElmType.AgentSecretVault, "version": 0, "attrs": vault_attrs})
        identity_res = await api_client.boss.put_object({"type": ElmType.AgentIdentity, "version": 0, "attrs": identity_attrs})
        my_identity_obj_id = identity_res["id"]
        vault_id = vault_res["id"]
        now_ms = str(int(asyncio.get_running_loop().time() * 1000))

        # ✅ THE FIX: Ensure all four associations have the required `attrs` field.
        await asyncio.gather(
            api_client.boss.put_association({"type": "has_secret_vault", "sourceId": my_identity_obj_id, "targetId": vault_id, "time": now_ms, "position": now_ms, "attrs": {}}),
            api_client.boss.put_association({"type": "owns_agent_identity", "sourceId": api_client.user_id, "targetId": my_identity_obj_id, "time": now_ms, "position": now_ms, "attrs": {"agentId": agent_uuid}}),
            api_client.boss.put_association({"type": agent_uuid, "sourceId": api_client.user_id, "targetId": my_identity_obj_id, "time": now_ms, "position": now_ms, "attrs": {}}),
            api_client.boss.put_association({"type": id_args.name, "sourceId": api_client.user_id, "targetId": my_identity_obj_id, "time": now_ms, "position": now_ms, "attrs": {"isDisplayName": "true"}})
        )
        
        client.logger.info(f"[Hydration] New identity '{id_args.name}' provisioned successfully.")
        my_identity_obj = await api_client.boss.get_object(ElmType.AgentIdentity, my_identity_obj_id)

    # --- Decrypt the Vault and load keys into memory ---
    my_id = my_identity_obj["attrs"]["agentId"]
    my_identity_obj_id = my_identity_obj["id"]
    
    vault_assoc = await api_client.boss.get_associations("has_secret_vault", my_identity_obj_id)
    if not vault_assoc or not vault_assoc.get("associations"):
        raise RuntimeError(f"FATAL: Could not find a secret vault associated with identity {my_identity_obj_id}.")
    
    vault_id = vault_assoc["associations"][0]["targetId"]
    vault_obj = await api_client.boss.get_object(ElmType.AgentSecretVault, vault_id)

    client.logger.info(f"[Hydration] Decrypting private keys from vault {vault_id}...")
    decrypted_id, local_kx_priv, local_sign_priv = decrypt_identity_from_vault_attrs(vault_obj["attrs"], IDENT_PASSWORD)
    kx_priv = local_kx_priv
    sign_priv = local_sign_priv
    
    if my_id != decrypted_id:
        client.logger.warning(f"Hydration ID mismatch. Overwriting in-memory ID with persistent ID from vault.")
        my_id = decrypted_id

    client.logger.info(f"[Hydration] Agent keys loaded for persistent ID {my_id[:8]}...")
    return my_id


async def bootstrap(config_path: str) -> dict:
    """
    Performs the mandatory pre-flight check for the agent.

    This function orchestrates the entire startup and validation sequence:
    1.  It loads the agent's configuration.
    2.  It creates a temporary, sterile, single-use user identity for testing.
    3.  It uses this temporary identity to run the full, live-fire `runSelfTests`
        suite against the live substrate.
    4.  If, and only if, all tests pass, it returns the agent's real,
        long-term credentials, signaling that the agent is cleared for startup.

    If any step fails, this function will raise an exception, halting the
    agent's startup process and preventing a broken agent from ever going online.

    Returns:
        A dictionary containing the 'base_url' and 'auth_creds' for the main agent.
    """
    print("======================================================")
    print("  HSAgent-2 BOOTSTRAP AND SELF-TEST SEQUENCE")
    print("======================================================")
    
    # 1. Load config to get the base URL and the agent's real credentials.
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        api_config = config.get("api", {})
        base_url = api_config.get("base_url")
        real_creds = api_config.get("credentials")
        if not base_url or not real_creds:
            raise RuntimeError(f"api.base_url and api.credentials not found in {config_path}")
    except FileNotFoundError:
        raise RuntimeError(f"Configuration file not found at: {config_path}")
    except Exception as e:
        raise RuntimeError(f"Failed to load or parse configuration: {e}")

    # 2. Provision a new, random, single-use user for the test.
    #    This ensures our test runs in a clean, isolated environment.
    test_creds = {
        "username": f"selftest-user-{uuid.uuid4().hex[:8]}",
        "password": secrets.token_hex(16)
    }
    print(f"[Bootstrap] Provisioning temporary test user: {test_creds['username']}")
    
    temp_api_client = SummonerAPIClient(base_url)
    try:
        # The login method will auto-register the user if they don't exist.
        await temp_api_client.login(test_creds)
        print("[Bootstrap] Temporary user provisioned successfully.")
    except Exception as e:
        print(f"❌ FATAL: Failed to provision temporary test user: {e}")
        raise
    finally:
        await temp_api_client.close()


    # 3. RUN THE SELF-TESTS for our adapters using the temporary credentials.
    #    This is the "pre-flight check." If this fails, it will raise an exception
    #    and the entire bootstrap process will halt.
    await runSelfTests(base_url, test_creds)
    
    print("\n[Bootstrap] Self-tests passed. Proceeding with agent startup.")
    print("======================================================")
    
    # 4. Return the real configuration for the main agent's mission.
    return {"base_url": base_url, "auth_creds": real_creds}

async def setup():
    """
    [MIGRATED] The master orchestrator for the agent's lifecycle.
    This function replaces the original, simple database setup with a full
    bootstrap, self-test, and provisioning sequence.
    """
    global API_CLIENT, STATE_STORE, NONCE_STORE_FACTORY, my_id

    # --- Act I: The Bootstrap ---
    # We run the pre-flight check to ensure the world is safe.

    print("======================================================")
    print("  HSAgent-2 BOOTSTRAP AND SELF-TEST SEQUENCE")
    print("======================================================")
    
    # 1. Load config to get the base URL.
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False)
    args, _ = parser.parse_known_args()
    config_path = args.config_path or "configs/client_config.json"

    with open(config_path, 'r') as f: config = json.load(f)
    api_config = config.get("api", {})
    base_url = api_config.get("base_url")
    if not base_url:
        raise RuntimeError(f"api.base_url not found in {config_path}")

    # 2. Provision a temporary, single-use user for the test.
    test_creds = {
        "username": f"selftest-user-{uuid.uuid4().hex[:8]}",
        "password": secrets.token_hex(16)
    }
    print(f"[Bootstrap] Provisioning temporary test user: {test_creds['username']}")
    
    temp_api_client = SummonerAPIClient(base_url)
    try:
        await temp_api_client.login(test_creds)
        print("[Bootstrap] Temporary user provisioned successfully.")
    finally:
        await temp_api_client.close()

    # 3. Run the self-tests. If this fails, it will raise an exception.
    await runSelfTests(base_url, test_creds)
    
    print("\n[Bootstrap] Self-tests passed. Proceeding with agent startup.")
    print("======================================================")

    # --- Act II: The Provisioning ---
    # The Quartermaster equips the agent for its mission.

    real_creds = api_config.get("credentials")
    if not real_creds:
        raise RuntimeError(f"api.credentials not found in {config_path}")

    API_CLIENT = SummonerAPIClient(base_url)
    await API_CLIENT.login(real_creds)
    client.logger.info(f"Main API Client initialized and authenticated as {API_CLIENT.username}")
    
    my_id = await hydrate_agent(API_CLIENT)
    
    STATE_STORE = SubstrateStateStore(api=API_CLIENT, self_agent_id=my_id)
    client.logger.info("Agent's SubstrateStateStore has been provisioned.")

    def nonce_store_factory(peer_id: str) -> HybridNonceStore:
        if not API_CLIENT or not my_id:
            raise RuntimeError("API Client and agent ID must be initialized before creating a nonce store.")
        return HybridNonceStore(api=API_CLIENT, self_id=my_id, peer_id=peer_id)

    NONCE_STORE_FACTORY = nonce_store_factory
    client.logger.info("Agent's HybridNonceStore factory has been provisioned.")

    # --- Act III: The Handoff ---
    # We start the main protocol client, using our "hack" to manage the event loops.

    client.logger.warning("Applying monkey patch to client.set_termination_signals() to prevent thread conflict.")
    client.set_termination_signals = lambda: None

    print("\n[Orchestrator] Handing control to the SummonerClient protocol loop...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='Relative path to the client config JSON (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    # Ensure DB schema before client loop starts.
    client.loop.run_until_complete(setup())

    try:
        client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        print("Oh dear, you are dead!")
