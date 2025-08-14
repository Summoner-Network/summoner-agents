
# =============================================================================
# Crypto Utilities for Summoner Handshake 
# =============================================================================
"""
This module provides cryptographic helpers for constructing and validating
Summoner handshake messages. It is storage-agnostic: the caller supplies
a `nonce_store` implementing:
    - exists(nonce: str) -> bool | Awaitable[bool]
    - is_expired(ts: datetime.datetime) -> bool
    - add(nonce: str, ts: datetime.datetime) -> None | Awaitable[None]
This allows integration with in-memory, database-backed, or distributed
nonce tracking without coupling to a specific persistence layer.

Features:
    - Base64 encoding/decoding
    - Public key serialization
    - X25519 key exchange + HKDF key derivation
    - Ed25519 signing and verification
    - Handshake message construction and validation

All timestamps are generated in local time using ISO 8601 format.

NOTE: For production-grade deployments, you should:
    - Bind public keys to identities (via PKI or out-of-band exchange)
    - Handle clock skew for timestamp validation
    - Use secure storage (KMS, HSM, or in-memory) for symmetric keys
    - Consider transcript/channel binding for stronger handshake integrity
"""
import os
import base64
import datetime
import json
import inspect
from typing import Union, Any, Optional

from cryptography.hazmat.primitives.asymmetric import x25519, ed25519
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# ------------------------
# Base64 helpers
# ------------------------

def b64_encode(data: bytes) -> str:
    """Encode bytes to Base64 string."""
    return base64.b64encode(data).decode("utf-8")


def b64_decode(data: str) -> bytes:
    """Decode Base64 string to bytes."""
    return base64.b64decode(data.encode("utf-8"))


# ------------------------
# Public key serialization
# ------------------------

def serialize_public_key(
    key: Union[x25519.X25519PublicKey, ed25519.Ed25519PublicKey]
) -> str:
    """Serialize a public key to raw bytes and Base64."""
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return b64_encode(raw)


# ------------------------
# KDF and signatures
# ------------------------

def derive_symmetric_key(
    priv_key: x25519.X25519PrivateKey,
    peer_pub_b64: str
) -> bytes:
    """Perform X25519 exchange and derive a 32-byte key with HKDF-SHA256."""
    peer_raw = b64_decode(peer_pub_b64)
    shared = priv_key.exchange(
        x25519.X25519PublicKey.from_public_bytes(peer_raw)
    )
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"handshake",
    )
    return hkdf.derive(shared)


def sign_payload(
    priv_sign: ed25519.Ed25519PrivateKey,
    data: bytes
) -> str:
    """Sign bytes with Ed25519 and return Base64(signature)."""
    sig = priv_sign.sign(data)
    return b64_encode(sig)


def verify_payload(
    pub_sign_b64: str,
    data: bytes,
    sig_b64: str
) -> bool:
    """Verify an Ed25519 signature. Raises on failure; returns True on success."""
    raw_pub = b64_decode(pub_sign_b64)
    raw_sig = b64_decode(sig_b64)
    verify_key = ed25519.Ed25519PublicKey.from_public_bytes(raw_pub)
    verify_key.verify(raw_sig, data)
    return True


# ------------------------
# Handshake message builders & validators
# ------------------------

def build_handshake_message(
    msg_type: str,                      # "init" or "response"
    nonce: str,                         # the peer must echo/expect this
    priv_kx: x25519.X25519PrivateKey,   # our X25519 private key
    priv_sign: ed25519.Ed25519PrivateKey
) -> dict:
    """
    Construct a signed handshake message with:
      - type, nonce, kx_pub, sign_pub, timestamp, sig
    Signature covers: f"{nonce}|{kx_pub_b64}|{timestamp}"
    """
    ts = datetime.datetime.now().replace(microsecond=0).isoformat()
    kx_pub_b64 = serialize_public_key(priv_kx.public_key())
    sign_pub_b64 = serialize_public_key(priv_sign.public_key())

    payload = f"{nonce}|{kx_pub_b64}|{ts}".encode("utf-8")
    sig_b64 = sign_payload(priv_sign, payload)

    return {
        "type": msg_type,
        "nonce": nonce,
        "kx_pub": kx_pub_b64,
        "sign_pub": sign_pub_b64,
        "timestamp": ts,
        "sig": sig_b64,
    }


async def _maybe_await(x: Any) -> Any:
    """Await if awaitable; otherwise return as-is. Lets us support sync/async stores."""
    return await x if inspect.isawaitable(x) else x


async def validate_handshake_message(
    msg: dict,
    expected_type: str,                 # "init" or "response"
    expected_nonce: str,                # what we expect to see in msg["nonce"]
    nonce_store: Any,                   # duck-typed store with exists(nonce), is_expired(ts), add(nonce, ts)
    priv_kx: x25519.X25519PrivateKey    # our X25519 private key to derive the symmetric key
) -> bytes:
    """
    Validate a signed handshake message and derive the symmetric key.

    Checks performed (raises ValueError on failure):
      1) Type is expected_type
      2) Timestamp parses
      3) Nonce matches expected_nonce
      4) Not replayed and not expired (using nonce_store)
      5) Ed25519 signature verifies over (nonce|kx_pub|timestamp)

    On success:
      - Records the nonce in nonce_store
      - Returns the derived 32-byte symmetric key
    """
    if not (isinstance(msg, dict) and msg.get("type") == expected_type):
        raise ValueError("Invalid message type")

    nonce = msg.get("nonce")
    ts_str = msg.get("timestamp")
    try:
        ts = datetime.datetime.fromisoformat(ts_str)
    except Exception:
        raise ValueError("Invalid timestamp format")

    if nonce != expected_nonce:
        raise ValueError("Nonce mismatch")

    if await _maybe_await(nonce_store.exists(nonce)) or nonce_store.is_expired(ts):
        raise ValueError("Replay or stale message")

    payload = f"{nonce}|{msg.get('kx_pub')}|{ts_str}".encode("utf-8")
    verify_payload(msg.get("sign_pub"), payload, msg.get("sig"))

    sym_key = derive_symmetric_key(priv_kx, msg.get("kx_pub"))
    await _maybe_await(nonce_store.add(nonce, ts))
    return sym_key


# ------------------------
# Secure envelope sealer & opener
# ------------------------

def seal_envelope(sym_key: bytes, sign_priv: ed25519.Ed25519PrivateKey, obj: dict) -> dict:
    """
    AEAD-encrypt + sign an application payload.
    - Computes SHA-256 over the plaintext (associated data)
    - Encrypts with AES-GCM using a fresh 12-byte nonce
    - Signs the JSON envelope with Ed25519
    Returns: {"envelope": {...}, "sig": "<b64>"}
    """
    plaintext = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    # SHA-256 fingerprint of plaintext (used as AEAD associated data)
    h = hashes.Hash(hashes.SHA256())
    h.update(plaintext)
    fingerprint = h.finalize()

    aes = AESGCM(sym_key)
    # 12-byte nonce for AES-GCM; caller can choose different nonce strategy if desired
    import secrets as _secrets
    nonce = _secrets.token_bytes(12)

    ciphertext = aes.encrypt(nonce, plaintext, associated_data=fingerprint)

    envelope = {
        "nonce": b64_encode(nonce),
        "ciphertext": b64_encode(ciphertext),
        "hash": b64_encode(fingerprint),
        "ts": datetime.datetime.now().replace(microsecond=0).isoformat(),
    }
    env_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig_b64 = b64_encode(sign_priv.sign(env_bytes))
    return {"envelope": envelope, "sig": sig_b64}


def open_envelope(sym_key: bytes, peer_sign_pub_b64: str, signed: dict) -> dict:
    """
    Verify + decrypt an application envelope produced by seal_envelope().
    - Verifies Ed25519 signature over the JSON envelope
    - Decrypts with AES-GCM using the embedded nonce and associated-data hash
    - Verifies the SHA-256 fingerprint matches the decrypted plaintext
    Returns the decoded dict payload.
    """
    envelope = signed.get("envelope", {})
    sig_b64 = signed.get("sig", "")

    env_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    verify_payload(peer_sign_pub_b64, env_bytes, sig_b64)

    nonce = b64_decode(envelope["nonce"])
    ciphertext = b64_decode(envelope["ciphertext"])
    fingerprint = b64_decode(envelope["hash"])

    aes = AESGCM(sym_key)
    plaintext = aes.decrypt(nonce, ciphertext, associated_data=fingerprint)

    h = hashes.Hash(hashes.SHA256())
    h.update(plaintext)
    if h.finalize() != fingerprint:
        raise ValueError("Hash mismatch after decrypt")

    return json.loads(plaintext.decode("utf-8"))

# ------------------------
# Save & load functions for readable JSON DID
# ------------------------

def save_identity_json(
    path: str,
    my_id: str,
    kx_priv: x25519.X25519PrivateKey,
    sign_priv: ed25519.Ed25519PrivateKey,
    password: Optional[bytes] = None,
) -> None:
    # Publics (always raw+b64)
    kx_pub_raw = kx_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    sign_pub_raw = sign_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    doc = {
        "my_id": my_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat() + "Z",
        "kx_pub_b64": b64_encode(kx_pub_raw),
        "sign_pub_b64": b64_encode(sign_pub_raw),
    }

    if password:
        # Encrypted PKCS#8 PEM
        kx_priv_pem = kx_priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password),
        )
        sign_priv_pem = sign_priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password),
        )
        doc["kx_priv_pem"] = kx_priv_pem.decode("utf-8")
        doc["sign_priv_pem"] = sign_priv_pem.decode("utf-8")
    else:
        # Raw + b64 (dev-friendly)
        kx_priv_raw = kx_priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        sign_priv_raw = sign_priv.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        doc["kx_priv_b64"] = b64_encode(kx_priv_raw)
        doc["sign_priv_b64"] = b64_encode(sign_priv_raw)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    # Restrict perms (best effort)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass

def load_identity_json(
    path: str,
    password: Optional[bytes] = None,
) -> tuple[str, x25519.X25519PrivateKey, ed25519.Ed25519PrivateKey, str, str]:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    my_id = doc["my_id"]

    # Private keys: accept either PEM (encrypted) or raw b64
    if "kx_priv_pem" in doc and "sign_priv_pem" in doc:
        kx_priv = serialization.load_pem_private_key(
            doc["kx_priv_pem"].encode("utf-8"), password=password
        )
        sign_priv = serialization.load_pem_private_key(
            doc["sign_priv_pem"].encode("utf-8"), password=password
        )
        # Type hints reassure: cast to exact classes
        assert isinstance(kx_priv, x25519.X25519PrivateKey)
        assert isinstance(sign_priv, ed25519.Ed25519PrivateKey)
    else:
        kx_priv = x25519.X25519PrivateKey.from_private_bytes(b64_decode(doc["kx_priv_b64"]))
        sign_priv = ed25519.Ed25519PrivateKey.from_private_bytes(b64_decode(doc["sign_priv_b64"]))

    kx_pub_b64 = doc["kx_pub_b64"]
    sign_pub_b64 = doc["sign_pub_b64"]

    return my_id, kx_priv, sign_priv, kx_pub_b64, sign_pub_b64


# ------------------------
# Save & load functions for encrypted JSON DID
# ------------------------

_ID_FILE_VERSION = "id.v1"
_ID_AAD = b"HSAgent.identity.v1"  # associated data bound into AES-GCM

def _kdf_scrypt(password: bytes, salt: bytes) -> bytes:
    """
    Derive a 32-byte key from a password using scrypt.
    n=2**14 is a good interactive default; adjust r/p for your environment.
    """
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(password)

def save_identity_json_encrypted(
    path: str,
    password: bytes,
    my_id: str,
    kx_priv: x25519.X25519PrivateKey,
    sign_priv: ed25519.Ed25519PrivateKey,
) -> None:
    # 1) Collect raw key bytes (never store unencrypted on disk)
    kx_priv_raw   = kx_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    kx_pub_raw    = kx_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    sign_priv_raw = sign_priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    sign_pub_raw  = sign_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # 2) Build the plaintext JSON (small, stable schema)
    plaintext_obj = {
        "my_id": my_id,
        "created_at": datetime.datetime.now(datetime.timezone.utc)\
            .replace(microsecond=0).isoformat(),
        "kx_priv_b64":   b64_encode(kx_priv_raw),
        "kx_pub_b64":    b64_encode(kx_pub_raw),
        "sign_priv_b64": b64_encode(sign_priv_raw),
        "sign_pub_b64":  b64_encode(sign_pub_raw),
    }
    plaintext = json.dumps(plaintext_obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    # 3) Derive key and seal with AES-GCM
    salt  = os.urandom(16)
    key   = _kdf_scrypt(password, salt)
    aes   = AESGCM(key)
    nonce = os.urandom(12)
    ct    = aes.encrypt(nonce, plaintext, associated_data=_ID_AAD)

    # 4) Store only metadata + ciphertext
    doc = {
        "v": _ID_FILE_VERSION,
        "kdf": "scrypt",
        "salt": b64_encode(salt),
        "nonce": b64_encode(nonce),
        "aad": b64_encode(_ID_AAD),
        "ciphertext": b64_encode(ct),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

def load_identity_json_encrypted(path: str, password: bytes):
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    if doc.get("v") != _ID_FILE_VERSION or doc.get("kdf") != "scrypt":
        raise ValueError("Unsupported identity file format")

    salt  = b64_decode(doc["salt"])
    nonce = b64_decode(doc["nonce"])
    aad   = b64_decode(doc["aad"])
    ct    = b64_decode(doc["ciphertext"])

    key = _kdf_scrypt(password, salt)
    aes = AESGCM(key)
    plaintext = aes.decrypt(nonce, ct, associated_data=aad)

    obj = json.loads(plaintext.decode("utf-8"))

    my_id = obj["my_id"]
    kx_priv   = x25519.X25519PrivateKey.from_private_bytes(b64_decode(obj["kx_priv_b64"]))
    sign_priv = ed25519.Ed25519PrivateKey.from_private_bytes(b64_decode(obj["sign_priv_b64"]))
    # Optional: you can also return the pub keys, but they can be recomputed.
    kx_pub_b64   = obj.get("kx_pub_b64")
    sign_pub_b64 = obj.get("sign_pub_b64")
    return my_id, kx_priv, sign_priv, kx_pub_b64, sign_pub_b64