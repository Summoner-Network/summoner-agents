# Summoner Decentralized Identifiers (DID) for Agents

**Version:** draft-0.1

**Scope:** This document defines the DID concept implemented in `HSAgent_1` as shown by the agent README, `crypto_utils.py`, and `agent.py`. It explains data structures, protocols, invariants, storage, and security properties. It does not assert compatibility with external DID frameworks. A short comparison section at the end maps Summoner terms to common blockchain and W3C-style taxonomies for interoperability only.


## 1. Abstract

Summoner agents maintain a long-term identity and establish pairwise authenticated sessions using signed handshake messages. Each session derives a symmetric key for optional message secrecy and integrity. Replay defense and state transitions are enforced using a nonce log and a small database schema. Identity material is saved locally in an encrypted JSON file. This paper specifies the identity format, handshake message, secure envelope, state rules, and threat model.



## 2. Terminology

* **Agent identity**: a stable record containing `my_id`, an X25519 key pair for key agreement, and an Ed25519 key pair for signatures. Persisted as an encrypted JSON file per agent name.
* **Handshake message `hs`**: a signed blob that binds a fresh nonce to the responder or initiator timestamp and a public key for key exchange.
* **Session key**: a 32-byte key derived with X25519 and HKDF-SHA256 when validating the first signed handshake in a cycle.
* **Secure envelope `sec`**: an optional message wrapper encrypted with AES-GCM and signed with Ed25519.
* **Role**: initiator or responder per peer. Roles are scoped per peer id.
* **Nonce**: a per-message freshness token. Nonces are recorded as sent or received. Duplicate received nonces are dropped. Handshake nonces have a TTL.



## 3. Identity model

### 3.1 Subject and keys

Each agent has:

* `my_id`: stable agent identifier. In the reference agent it is a UUID string.
* `kx_priv` and `kx_pub`: X25519 key pair for key agreement.
* `sign_priv` and `sign_pub`: Ed25519 key pair for authentication and signing.

Keys are created on first run per `--name` and saved to `id_agent_<name>.json` using password protection. The password in the demo is derived from the name. In production, supply a strong passphrase or a KMS binding.

### 3.2 Identity file: encrypted JSON

The file stores only metadata and ciphertext at rest. Private keys never appear unencrypted on disk. The format is:

```json
{
  "v": "id.v1",
  "kdf": "scrypt",
  "salt": "<b64>",
  "nonce": "<b64 12B>",
  "aad": "<b64 of literal HSAgent.identity.v1>",
  "ciphertext": "<b64>"
}
```

The ciphertext decrypts to the JSON object:

```json
{
  "my_id": "<uuid>",
  "created_at": "<ISO8601 UTC>",
  "kx_priv_b64": "<b64 raw 32B>",
  "kx_pub_b64": "<b64 raw 32B>",
  "sign_priv_b64": "<b64 raw 32B>",
  "sign_pub_b64": "<b64 raw 32B>"
}
```

**Algorithms:**

* Key derivation: scrypt with parameters n=2^14, r=8, p=1, length=32.
* AEAD encryption: AES-GCM with a random 12-byte nonce and associated data `HSAgent.identity.v1`.

### 3.3 DID statement (Summoner)

A Summoner DID is the tuple `(my_id, sign_pub, kx_pub)` held by an agent and saved in an encrypted identity file. There is no on-chain registry in this specification. Resolution is local to the agent runtime and any application namespace that binds `my_id` values to human-readable names.



## 4. Handshake protocol

### 4.1 Message layers

Plain fields: `intent`, `to`, `from`, `my_nonce`, `your_nonce`, `my_ref`, `your_ref`.

Crypto fields:

* `hs`: signed handshake blob with public keys and a bound nonce.
* `sec`: sealed envelope that optionally replaces plaintext `message`.

### 4.2 Signed handshake `hs`

The first authenticated message per cycle includes a handshake blob. The initiator sends `hs(type="init")` on the first `request`. The responder sends `hs(type="response")` on `confirm`.

**Schema:**

```json
{
  "type": "init" | "response",
  "nonce": "<echo target>",
  "kx_pub": "<b64>",
  "sign_pub": "<b64>",
  "timestamp": "<ISO8601>",
  "sig": "<b64 Ed25519 over nonce|kx_pub|timestamp>"
}
```

**Validation checks:**

1. `type` equals the expected value for the direction.
2. `timestamp` parses.
3. `nonce` equals the expected echo target.
4. Replay defense and staleness check via a nonce store with TTL.
5. Signature verifies using Ed25519 on the string `f"{nonce}|{kx_pub}|{timestamp}"`.

**On success:**

* Record the handshake nonce as received.
* Derive `sym_key = HKDF_SHA256(X25519(priv_kx, peer_kx_pub), info="handshake", length=32)`.
* Cache the peer signing public key for secure envelopes.

### 4.3 Session key lifecycle

* The session key is computed once per handshake cycle and stored in RAM at `SYM_KEYS[(role, peer_id)]`.
* The key is discarded on process exit. A new handshake will derive a fresh key.

### 4.4 Nonce rules

* Each side emits a fresh `my_nonce` per send step and records it as `sent`.
* Each received `my_nonce` is recorded exactly once as `received` using an idempotent insert.
* Duplicate `received` nonces are dropped. The message is ignored.
* Handshake nonces are also subject to a TTL window in the nonce store.

### 4.5 Finalize rules

* `conclude(my_ref)` then `finish(your_ref, my_ref)` then `close(your_ref, my_ref)`.
* On successful `close`, all `NonceEvent` rows for the pair are deleted.



## 5. Secure envelope `sec`

Once a session key exists, plaintext `message` may be replaced by a secure envelope.

**Seal algorithm:**

1. Serialize `obj` to a canonical JSON string with sorted keys.
2. Compute `fingerprint = SHA256(plaintext)`.
3. Encrypt with AES-GCM using a fresh 12-byte nonce and associated data `fingerprint`.
4. Build `envelope` with `nonce`, `ciphertext`, `hash`, and `ts`.
5. Sign `JSON(envelope)` with Ed25519 to produce `sig`.

**Envelope schema:**

```json
{
  "envelope": {
    "nonce": "<b64 12B>",
    "ciphertext": "<b64>",
    "hash": "<b64 sha256(plaintext)>",
    "ts": "<ISO8601>"
  },
  "sig": "<b64 Ed25519 over JSON(envelope)>"
}
```

**Open algorithm:**

1. Verify Ed25519 signature over `JSON(envelope)` using the peer signing key.
2. Decrypt with AES-GCM using the embedded `nonce` and associated data `hash`.
3. Recompute SHA-256 on the plaintext and check equality with `hash`.
4. Return the decoded JSON object. When the object has `{"message": "..."}` the agent surfaces it as `content["message"]`.



## 6. State machine and storage

### 6.1 Roles and routes

Initiator route:

```
init_ready -> init_exchange -> init_finalize_propose -> init_finalize_close -> init_ready
```

Responder route:

```
resp_ready -> resp_confirm -> resp_exchange -> resp_finalize -> resp_ready
```

### 6.2 Database model

* `RoleState(self_id, role, peer_id, state, local_nonce, peer_nonce, local_reference, peer_reference, exchange_count, finalize_retry_count, peer_address, [peer_sign_pub, peer_kx_pub, hs_derived_at, last_secure_at])`
* `NonceEvent(self_id, role, peer_id, flow, nonce)` with indexes for dedupe and cleanup.

### 6.3 Replay defense and cleanup

* The handshake validator uses `DBNonceStore` that consults `NonceEvent` for `flow="received"` and enforces a TTL.
* After a valid `close`, the nonce log for the pair is deleted.



## 7. Threat model and security properties

### 7.1 Goals

* Authenticate the peer across the first messages in each cycle using a signed handshake and nonce echo.
* Derive a fresh symmetric key per cycle without exposing long-term private keys over the wire.
* Provide optional message secrecy and integrity using AES-GCM and Ed25519 signatures on envelopes.
* Prevent trivial replay by recording received nonces and using a TTL for handshake nonces.

### 7.2 Out of scope for this draft

* Binding `my_id` to a human or organization beyond local files. No global registry is defined here.
* Clock skew handling beyond parsing. Applications may tighten validation windows.
* Key rotation and revocation. Future work may define rotations and migrations between identity files.
* Transcript or channel binding beyond the included nonce and public key binding in the signature payload.

### 7.3 At-rest protection

* Identity files are encrypted with AES-GCM using a key derived by scrypt. Applications should supply strong passwords or delegate to a key manager.

### 7.4 In-memory handling

* Session keys and peer signing keys live in RAM maps keyed by `(role, peer_id)`.
* The session key is never persisted to disk by this agent.



## 8. Multi-peer behavior

A single process may talk to many peers. All invariants are enforced per `(self_id, role, peer_id)` tuple. Session keys, signing keys, nonces, and references are independent per peer.



## 9. Practical guidance

* Use a unique `--name` per process. Reusing names shares identity and database files and may cause confusing state transitions.
* If a name was reused, stop agents and remove the matching identity and database files or start with a new name.
* Keep `PERSIST_CRYPTO` enabled. It is safe when optional columns are present. It is a no-op otherwise.



## 10. Compatibility notes and taxonomy mapping

This section serves only as a comparison for compatibility. Summoner DIDs are defined solely by the specifications above.

* **Subject identifier**: Summoner uses `my_id` as a local subject identifier. In W3C DID terms this is similar to a DID subject identifier but it is not bound to a DID method string.
* **Authentication key**: `sign_pub` is the verification key for Ed25519. This aligns with an authentication verification method.
* **Key agreement**: `kx_pub` is the X25519 public key used to derive session keys. This aligns with a key agreement verification method.
* **Service entries**: Not defined in this draft. Endpoints are established out of band by the Summoner client and server configuration.
* **Method operations**: Create and load are local file operations. There is no global DID document resolution in this draft.

Applications integrating with external DID tooling can treat `(my_id, sign_pub, kx_pub)` as the minimal record for mapping to a DID-like document while keeping Summoner semantics intact.



## 11. Future work

* Key rotation semantics with continuity proofs pinned by signatures.
* Optional transcript binding or channel binding to transport parameters.
* Optional inclusion of short-lived ephemeral X25519 keys while keeping long-term signing keys.
* Formalization of reconnect semantics in terms of session resumption tickets or references.



## 12. Appendix A: Reference algorithms and function mapping

* Identity load and save: `load_identity_json_encrypted`, `save_identity_json_encrypted`.
* Handshake construction: `build_handshake_message(type, nonce, kx_priv, sign_priv)`.
* Handshake validation and key derivation: `validate_handshake_message(msg, expected_type, expected_nonce, nonce_store, priv_kx)` returns `sym_key`.
* Envelope seal and open: `seal_envelope(sym_key, sign_priv, obj)` and `open_envelope(sym_key, peer_sign_pub_b64, signed)`.
* Nonce store used for handshake TTL and dedupe: `DBNonceStore` over `NonceEvent` with a default 60 second TTL.



## 13. Appendix B: Example objects

### 13.1 Example handshake (init)

```json
{
  "type": "init",
  "nonce": "6f2c...",
  "kx_pub": "CwJm1Jm...",
  "sign_pub": "5x6pQJ...",
  "timestamp": "2025-11-11T10:15:30",
  "sig": "aGZK..."
}
```

### 13.2 Example secure envelope

```json
{
  "envelope": {
    "nonce": "b64-12B",
    "ciphertext": "b64-ct",
    "hash": "b64-sha256",
    "ts": "2025-11-11T10:15:35"
  },
  "sig": "b64-ed25519"
}
```

### 13.3 Example decrypted payload

```json
{ "message": "How are you?" }
```



## 14. Compliance checklist for implementations

* Identity file uses scrypt and AES-GCM. Private keys never appear unencrypted on disk.
* First authenticated message per cycle includes a signed handshake with a nonce echo.
* HKDF-SHA256 on X25519 shared secret yields a 32 byte session key with `info="handshake"` and no salt.
* Nonce replay defense uses a per-pair `NonceEvent` table and a TTL window for handshake nonces.
* Optional secure envelope uses Ed25519 over the JSON envelope and AES-GCM with the SHA-256 of plaintext as associated data.
* On `close` the nonce log is deleted for the pair.


