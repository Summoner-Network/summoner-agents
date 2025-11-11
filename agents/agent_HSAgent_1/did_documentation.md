# Summoner Decentralized Identifiers (DID) for Agents

**Version:** draft-0.2

**Scope:** This document defines the DID concept used in [`HSAgent_1`](readme.md). It explains data structures, protocols, invariants, storage, and security properties. It does not assert compatibility with external DID frameworks. A short comparison section at the end maps Summoner terms to common blockchain and W3C-style taxonomies for interoperability only.


## 1. Abstract

Summoner agents maintain a long-term identity and establish pairwise authenticated sessions using signed handshake messages. Each session derives a symmetric key for optional message secrecy and integrity. Replay defense and state transitions are enforced using a nonce log and a small database schema. Identity material is saved locally in an encrypted JSON file. This paper specifies the identity format, handshake message, secure envelope, state rules, and threat model.



## 2. Terminology

* **Agent identity**: a stable record containing `my_id`, an [X25519](https://en.wikipedia.org/wiki/Curve25519) key pair for key agreement, and an [Ed25519](https://ed25519.cr.yp.to/) key pair for signatures. Persisted as an encrypted JSON file per agent name.
* **Handshake message `hs`**: a signed blob that binds a fresh nonce to the responder or initiator timestamp and a public key for key exchange.
* **Session key**: a 32-byte key derived with X25519 and [HKDF](https://en.wikipedia.org/wiki/HKDF)-[SHA256](https://en.wikipedia.org/wiki/SHA-2) when validating the first signed handshake in a cycle.
* **Secure envelope `sec`**: an optional message wrapper encrypted with [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) and signed with Ed25519.
* **Role**: initiator or responder per peer. Roles are scoped per peer id.
* **Nonce**: a per-message freshness token. Nonces are recorded as sent or received. Duplicate received nonces are dropped. Handshake nonces have a TTL.



# 3. Identity model

### 3.1 Design rationale

The Summoner DID is deliberately minimal: a stable identifier `my_id` and two long-term key pairs with distinct purposes. The split between signing ([Ed25519](https://ed25519.cr.yp.to/)) and key agreement ([X25519](https://en.wikipedia.org/wiki/Curve25519)) creates clear semantics. Authentication maps to the signing key, while confidentiality for message payloads is obtained from key agreement plus [HKDF](https://en.wikipedia.org/wiki/HKDF). This separation limits key reuse across operations and makes auditing simpler because each cryptographic step has one designated key.

**Why keep long-term keys if the session key is per-handshake?** Long-term keys provide a durable anchor for recognition across restarts. An agent that restarts with the same identity file can reestablish trust with peers without an out-of-band enrollment step. In distributed settings where processes are short-lived or migrate between hosts, this reduces friction and aligns with the operational model of Summoner's client/server.

Our stable agent identifier (`my_id`) is intentionally independent of the public keys. The identifier is what applications use to route and label traffic; the keys prove continuity of control over that identifier. This decoupling gives room for future rotations without breaking application-level references to the agent, and conversely enables renaming identifiers without discarding cryptographic history. It also avoids accidental use of a public key as a transport address or database key.

> [!NOTE]
> The current demo uses long-term [X25519](https://en.wikipedia.org/wiki/Curve25519) and [Ed25519](https://ed25519.cr.yp.to/). This yields simple continuity semantics but shifts the responsibility for key protection to the host that stores the identity file. The benefits are strong in operability, but the security envelope is only as good as the storage and passphrase practices described below.

**Subject and keys:**

Each agent has:

* `my_id`: stable agent identifier. In the reference agent it is a UUID string.
* `kx_priv` and `kx_pub`: [X25519](https://en.wikipedia.org/wiki/Curve25519) key pair for key agreement.
* `sign_priv` and `sign_pub`: [Ed25519](https://ed25519.cr.yp.to/) key pair for authentication and signing.

Keys are created on first run per `--name` and saved to `id_agent_<name>.json` using password protection. The password in the demo is derived from the name. In production, supply a strong passphrase or a KMS binding.

### 3.2 Identity file: security and operability

The encrypted JSON identity file is the source of truth for an agent's DID material. It aims to balance at-rest protection with straightforward operations.

#### Security properties

* **Confidentiality at rest.** Private keys are never written in the clear. The JSON payload containing `my_id`, public keys, and raw private keys is sealed with [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) using a key derived by scrypt (see [NIST recommendations](https://nvlpubs.nist.gov/nistpubs/legacy/sp/nistspecialpublication800-38d.pdf)). Associated data binds the ciphertext to a fixed context string (`HSAgent.identity.v1`) so accidental cross-context decryption is rejected.
* **Integrity and versioning.** A version tag and fixed associated data allow strict parsing and upgrade checks. If an incompatible file is encountered, agents fail closed rather than partially accepting it.

#### Operational properties

* **Predictable startup.** One file per `--name` keeps lifecycle management trivial: create, backup, restore. Scripts can manage identities using standard file operations.
* **Human-auditable after decrypt.** The plaintext structure is small and explicit: identifier, creation time, and base64-encoded key material. In an incident response scenario, a single decrypt is sufficient to inspect what the agent believes its identity is.

#### Identity file: encrypted JSON

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

#### Algorithms

* Key derivation: scrypt with parameters n=2^14, r=8, p=1, length=32.
* AEAD encryption: [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) with a random 12-byte nonce and associated data `HSAgent.identity.v1`.

> [!CAUTION]
> **Operational cautions:**
>
> * **Shared `--name` collapses identities.** Running multiple processes with the same `--name` makes them share the same identity and database. This may be convenient for local tests but is hazardous in production because it merges distinct agents into one DID from the system's point of view.
> * **Passphrase source.** In the demo, the passphrase is derived from `--name`. This is intentionally weak for convenience. In production the passphrase must come from an OS keychain or KMS and must not appear in code, logs, or shell history.


### 3.3 DID statement and persistence scope

<!-- A Summoner DID is the tuple `(my_id, sign_pub, kx_pub)` held by an agent and saved in an encrypted identity file. There is no on-chain registry in this specification. Resolution is local to the agent runtime and any application namespace that binds `my_id` values to human-readable names. -->

In Summoner terms, a DID is the tuple `(my_id, sign_pub, kx_pub)` held by an agent and saved in its encrypted identity file.

* **Local resolution (no registry).** There is no on-chain or global registry in this specification. "Resolving" a DID means loading the agent's identity file, deriving the key material, and exposing the tuple to the runtime (and to any application namespace that binds `my_id` values to human-readable names).
* **Proof of control.** Control is demonstrated by producing valid [Ed25519](https://ed25519.cr.yp.to/) signatures under `sign_priv` and by successfully completing a handshake that uses `kx_priv` to derive the session key.
* **Portability.** Moving an identity between hosts is a file copy plus the passphrase. Disaster recovery is straightforward as long as password hygiene is strong and backups are handled carefully.
* **Scoping.** The tuple is sufficient for peer recognition and for envelope verification and decryption. No additional metadata is required by the protocol in this draft.

> [!IMPORTANT]
> Decoupling `my_id` from keys enables rotation in principle, but rotation semantics are not defined in this draft. Without a documented rotation flow, operators must treat the identity file as effectively immutable during the lifetime of an agent deployment.


### 3.4 Alternatives considered

* **Ephemeral-only keys.** Using ephemeral key pairs per session simplifies rotation and improves forward secrecy. It also complicates recognizability across restarts and frustrates allowlist workflows that expect stable material. The project favors long-term keys to reduce operational overhead. See [§3.5](#35-key-lifecycle-and-forward-secrecy-considerations) for forward-secrecy implications.
* **Single key pair for both signing and key agreement.** Reduces footprint but couples distinct semantics, increases risk of cross-protocol misuse, and weakens audit clarity. Summoner keeps signing and agreement separate for simplicity, least privilege, and reviewability.
* **Identifier derived from keys.** Binding `my_id` to a hash of the public keys simplifies verification but couples identity to a specific key set. This makes renaming or rotating a single key harder. Summoner keeps the identifier independent.



### 3.5 Key lifecycle and forward-secrecy considerations

The current demo keeps both [Ed25519](https://ed25519.cr.yp.to/) and [X25519](https://en.wikipedia.org/wiki/Curve25519) keys long-term in the identity file. Handshakes derive the session key using X25519 and [HKDF](https://en.wikipedia.org/wiki/HKDF). Because the demo uses the same long-term X25519 key across cycles and does not inject fresh randomness into HKDF, two consequences follow:

1. **Session-key repeatability between the same pair.** If both peers use the same long-term X25519 keys, the X25519 shared secret is constant for that pair. With `salt=None` and fixed `info="handshake"`, HKDF will derive the same 32-byte value on every cycle. The code treats this as a "session" key and re-derives it at each handshake, but the value will repeat unless one side rotates keys.
2. **Forward secrecy.** If a long-term X25519 private key is later compromised, past envelope traffic that was captured can be decrypted because the derived key is constant. True forward secrecy requires either ephemeral X25519 keys per cycle or a salt that changes unpredictably per handshake and is committed consistently at both sides.

These are acceptable trade-offs for the stated goal of an orchestration/state demo with a "strong crypto veneer." For production, two incremental hardening options are consistent with the current design:

* **Ephemeral X25519 per cycle** while keeping the Ed25519 signing key long-term. This preserves recognizability via signatures and yields fresh [ECDH](https://en.wikipedia.org/wiki/Elliptic-curve_Diffie%E2%80%93Hellman) inputs for HKDF each cycle.
* **Per-cycle HKDF salt** derived from jointly known freshness material that is already validated, such as a function of both sides' nonces and the timestamp inside `hs`. This keeps the long-term X25519 keys but makes the derived key change per cycle.

Either option stays within Summoner semantics and does not require importing external frameworks.

### 3.6 Operational practices

* **Unique names per process.** Treat `--name` as part of the security perimeter. Unique values avoid unintended state coupling in the database and unintended identity sharing.
* **Backups.** Back up the encrypted identity file and the passphrase separately. Periodically validate restores in a non-production environment to ensure operators can recover an agent without changing its DID.
* **Logging boundaries.** Never log raw private key material. Current code adheres to this by limiting logs to short hexdigests of derived symmetric keys and metadata timestamps.
* **Least-access deployment.** Run agents under system users with restricted filesystem permissions so identity files are not broadly readable. The code attempts to set `chmod 600`; enforce this at the OS level too.



### 3.7 Known limitations and near-term hardening

* **No documented rotation flow.** While `my_id` is independent from keys, rotation and revocation semantics are out of scope in this draft. Operators cannot yet roll keys without out-of-band coordination.
* **Repeatable session key between the same pair.** As analyzed in [§3.5](#35-key-lifecycle-and-forward-secrecy-considerations), the current demo does not guarantee fresh derived keys per cycle when both peers keep static [X25519](https://en.wikipedia.org/wiki/Curve25519) keys.
* **Passphrase handling in the demo.** The demo derives the passphrase from `--name`. This must be replaced in real deployments with a secret manager or OS keychain.
* **No global registry or discovery.** Resolution is local. This is by design in Summoner, but integrators who need global lookups must build their own mapping layer above `(my_id, sign_pub, kx_pub)`.

>[!NOTE]
> Despite these limits, the model is coherent: a compact DID tuple, a single encrypted file as the persistence boundary, and explicit roles for signing and key agreement. The choices optimize for clear reasoning, low operator burden, and compatibility with the handshake and envelope logic defined elsewhere in this document.
## 4. Handshake protocol

This section describes how two agents authenticate one another at the start of each cycle and how they agree on a shared session key. The handshake adds cryptographic material on top of the normal routing fields so that identity and freshness are checked before any secure exchange occurs.



### 4.1 Message layers

**Plain fields (routing and control).**
`intent`, `to`, `from`, `my_nonce`, `your_nonce`, `my_ref`, `your_ref`.

**Cryptographic fields (added when security is active).**

* `hs` — a signed handshake blob that advertises public keys and binds a fresh nonce.
* `sec` — a sealed envelope that, when present, replaces the plaintext `message`.

These layers are orthogonal: routing continues to work even without crypto fields, while the crypto fields add authentication and, if enabled, confidentiality and integrity.



### 4.2 Signed handshake `hs`

**When it appears.**
The first authenticated message in a cycle carries a handshake blob:

* The **initiator** attaches `hs(type="init")` on its **first** `request`.
* The **responder** attaches `hs(type="response")` on its `confirm`.

**Schema.**

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

**What is being proven.**
The sender proves control of `sign_priv` by signing the tuple `nonce|kx_pub|timestamp`. The receiver checks that the `nonce` equals the expected echo target and that the `type` matches the direction (`init` for initiator, `response` for responder). This ties the proof of possession to the specific, fresh exchange in progress.

**Validation checks.**

1. The `type` matches the expected value for the direction.
2. The `timestamp` parses.
3. The `nonce` equals the expected echo target.
4. A replay/staleness decision is made using a nonce store with a TTL.
5. The Ed25519 signature verifies over `f"{nonce}|{kx_pub}|{timestamp}"`.

**On success.**

* The handshake nonce is recorded as "received" for replay accounting.
* A session key is derived as
  `sym_key = HKDF-SHA256( X25519(priv_kx, peer_kx_pub), info="handshake", length=32, salt=None )`.
* The peer’s signing public key is cached so envelopes can be verified.



### 4.3 Session key lifecycle

**Creation and scope.**
Exactly one session key is computed per handshake cycle and stored in RAM at `SYM_KEYS[(role, peer_id)]`. It is used to seal outgoing messages and to open incoming envelopes.

**End of life.**
The key is never written to disk by this agent. It is discarded when the process exits. A new authenticated cycle derives a new key.



### 4.4 Nonce rules

**Freshness on send.**
Each side emits a fresh `my_nonce` at each send step and records it as `sent`.

**Idempotent receive.**
Each received `my_nonce` is recorded exactly once as `received`. Duplicate `received` values are treated as replays and the message is ignored.

**Handshake window.**
Handshake nonces are additionally subject to a TTL in the nonce store so that late or repeated handshakes are rejected even if routing appears consistent.



### 4.5 Finalize rules

**Order and checks.**
Finalization proceeds in a fixed order—`conclude(my_ref)`, then `finish(your_ref, my_ref)`, then `close(your_ref, my_ref)`—with explicit reference matching at each step.

**Cleanup.**
On a successful `close`, all `NonceEvent` rows for the pair are deleted to reset transient history for the next cycle.



## 5. Secure envelope `sec`

This section specifies how an application payload can be carried confidentially and with strong integrity once a session key exists. The envelope is self-describing, signed, and authenticated so that corruption is detected before decryption and plaintext is revealed only to the intended peer.



### 5.1 Seal (sender)

**Algorithm.**

1. Serialize the payload object `obj` to canonical JSON (sorted keys).
2. Compute `fingerprint = SHA-256(plaintext)`. This serves as associated data.
3. Encrypt with AES-GCM using the session key, a fresh 12-byte nonce, and `fingerprint` as the associated data.
4. Build `envelope = { nonce, ciphertext, hash: b64(sha256), ts }`.
5. Sign `JSON(envelope)` with Ed25519 to produce `sig`.

**Envelope schema.**

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

**Why both AEAD and a signature.**
AES-GCM already authenticates ciphertext under the session key. The additional Ed25519 signature over the JSON envelope gives explicit, peer-verifiable provenance of the envelope structure itself and enables clear failure modes in logs before any decryption occurs.



### 5.2 Open (receiver)

**Algorithm.**

1. Verify the Ed25519 signature over `JSON(envelope)` using the cached peer signing key.
2. Decrypt with AES-GCM using the embedded `nonce` and the `hash` as associated data.
3. Recompute `SHA-256(plaintext)` and confirm it equals `hash`.
4. Decode the JSON payload and return it. If the decoded object contains `{"message": "..."}`, the agent surfaces that text as `content["message"]`.

**Error handling.**
If signature verification fails, if decryption fails, or if the hash check fails, the envelope is rejected and treated as an invalid message. The agent logs the failure and continues operating on plaintext-only content where policy allows.



### 5.3 When to use envelopes

Envelopes are optional. They should be enabled when confidentiality and tamper evidence are required for message bodies. When envelopes are disabled, the handshake still authenticates peers and derives a session key, but plaintext payloads are not protected in transit.


## 6. State machine and storage

This section explains how an agent progresses through a conversation (the "state machine") and how the agent records just enough data to keep that conversation safe and consistent (the "storage model"). 



### 6.1 Roles and routes

**Two roles, two routes.** Every agent may act as an **initiator** or a **responder** with a given peer. Each role has a small, loop-shaped path of states. These paths are the same every time, which makes behavior predictable and easy to reason about.

**Initiator route**

```
init_ready -> init_exchange -> init_finalize_propose -> init_finalize_close -> init_ready
```

* **init_ready.** Idle and waiting.
* **init_exchange.** Ping–pong using nonces. The initiator sends a request with a fresh `my_nonce`; the responder must echo it back as `your_nonce`.
* **init_finalize_propose.** The initiator proposes to conclude by sending its reference (`my_ref`).
* **init_finalize_close.** The initiator closes after the responder returns its reference.
* **Back to init_ready.** The thread resets, ready for the next cycle with the same peer.

**Responder route**

```
resp_ready -> resp_confirm -> resp_exchange -> resp_finalize -> resp_ready
```

* **resp_ready.** Idle and waiting.
* **resp_confirm.** The responder sends a confirmation with its own `my_nonce`.
* **resp_exchange.** Ping–pong using nonces while exchanging messages.
* **resp_finalize.** The responder returns its reference and waits for `close`.
* **Back to resp_ready.** Cleanup completes; ready for the next cycle.

**Why this structure matters.** Having a fixed sequence makes it easy to enforce the **echo rule** (your last `my_nonce` must come back as `your_nonce`) and the **finalize rule** (conclude → finish → close must match). These rules prevent out-of-order messages and make replay attempts obvious.



### 6.2 Database model

The database holds the minimal state needed to drive the routes and defend against simple attacks.

**Primary table: `RoleState`**
One row per conversation thread, keyed by `(self_id, role, peer_id)`.

```python
RoleState(
  self_id, role, peer_id, state,
  local_nonce, peer_nonce,
  local_reference, peer_reference,
  exchange_count, finalize_retry_count,
  peer_address,
  [peer_sign_pub, peer_kx_pub, hs_derived_at, last_secure_at]  # optional crypto metadata
)
```

* **state.** Where we are on the route (for example, `init_exchange`).
* **local_nonce / peer_nonce.** The last nonce we sent and the last nonce we saw from the peer. These drive the echo checks.
* **local_reference / peer_reference.** Short tokens exchanged during finalize to close the cycle cleanly.
* **exchange_count / finalize_retry_count.** Small counters to bound loops and retries so we do not get stuck.
* **peer_address.** Best-effort convenience field for logging and troubleshooting.
* **optional crypto metadata.** When present, we record the peer’s signing key, the peer’s key-exchange key, and timestamps for when a secure key was derived and last used.

**Replay log: `NonceEvent`**
A fast, append-only record of nonces seen per pair.

```python
NonceEvent(self_id, role, peer_id, flow, nonce)
```

* **flow.** Either `"sent"` or `"received"`.
* **Indexes.** We index by `(self_id, role, peer_id)` and by `(self_id, role, peer_id, flow, nonce)` so we can quickly detect duplicates and quickly delete a whole thread’s entries on close.

**Why two tables.** `RoleState` is the "current snapshot" for a thread. `NonceEvent` is the "flight recorder" used for replay checks and cleanup. Keeping them separate keeps queries simple and predictable.



### 6.3 Replay defense and cleanup

**How the replay check works.**
When a signed handshake arrives, the validator uses `DBNonceStore` to consult `NonceEvent` and decide two things:

1. **Has this nonce been seen before?** If yes, the message is treated as a replay.
2. **Is the handshake fresh?** We apply a **time-to-live window** (default sixty seconds). If the timestamp is too old, the validator rejects it as stale.

**What gets recorded.**
Every time we accept a peer’s `my_nonce`, we record it exactly once as `flow="received"`. If the same value shows up again, we can ignore it safely.

**When we clean up.**
On a successful `close`, we delete all `NonceEvent` rows for the `(self_id, role, peer_id)` pair. That resets the exchange history so the next cycle starts cleanly. The `RoleState` row remains, which lets the agent reconnect efficiently while keeping transient nonces out of the way.

**Why this is enough for the draft.**
The nonce echo, the sixty-second handshake window, and the per-pair replay log together stop trivial replays and make accidental duplicates harmless. The design stays minimal—no global registries, no complex clocks—while providing clear, auditable behavior that operators can reason about.



## 7. Threat model and security properties

**Purpose.** This section defines what the system protects, which adversaries it considers, the controls that enforce those protections, and where this draft leaves deliberate gaps. 



### 7.1 Goals

**Peer authentication on the first messages of each cycle.**
Each cycle begins with a signed handshake that binds a fresh nonce. The receiver validates the [Ed25519](https://ed25519.cr.yp.to/) signature and confirms the expected nonce echo before advancing the state machine.

**Confidentiality for each cycle.**
At the start of a cycle, the peers derive a symmetric key using [X25519](https://en.wikipedia.org/wiki/Curve25519) and [HKDF](https://en.wikipedia.org/wiki/HKDF). Long-term private keys are never transmitted over the network.

**Optional secrecy and integrity for payloads.**
When enabled, messages are carried inside a sealed envelope. [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) provides confidentiality and integrity for the plaintext. An Ed25519 signature over the canonical JSON envelope makes tampering detectable before decryption.

**Replay resistance.**
The system records every received nonce and enforces a time-to-live window for handshake nonces. Late or duplicated handshakes are rejected.

#### 7.1.1 Assets and trust boundaries

**Assets.**
The system protects several concrete artifacts:

* **Encrypted identity file on disk.** This file contains the agent's DID material and is the root of trust at startup.
* **Long-term private keys in memory.** After decryption, the [Ed25519](https://ed25519.cr.yp.to/) signing key and the [X25519](https://en.wikipedia.org/wiki/Curve25519) key-agreement key reside in RAM for runtime use.
* **Per-pair session keys in memory.** A 32-byte symmetric key is derived for each `(role, peer_id)` pair and kept only for the duration of the run.
* **Peer public keys.** The peer's Ed25519 public key is learned during the signed handshake. It is cached in memory and may be persisted to the database for convenience.
* **Per-pair nonce logs in the database.** `NonceEvent` records the nonces observed for each conversation pair and supports replay detection and cleanup on `close`.

**Trust boundaries.**
The operating environment is divided into clear protection domains:

* **Filesystem boundary.** Access to the encrypted identity file is controlled by OS permissions. This boundary ensures at-rest confidentiality of private keys.
* **Database boundary.** The local database houses `NonceEvent` and `RoleState`. Integrity of this store underpins replay resistance and state progression.
* **Network boundary.** Summoner clients exchange messages across an untrusted network. All input received at this boundary must pass handshake authentication, nonce checks, and (when enabled) envelope verification before it influences state.


#### 7.1.2 Assumptions

**Process trust.** The agent is permitted to hold decrypted key material in RAM while it runs.

**Time source.** The system clock is reasonably accurate. Timestamps are parsed, but clock drift is not corrected in this draft.

**Secrets input.** The passphrase used to decrypt the identity file is strong, or it is supplied by a secure source such as an operating-system keychain or a key management service.

**Transport model.** The transport offers best-effort delivery. Message loss is tolerable because protocol progress relies on explicit nonces and references rather than implicit sequence numbers.



### 7.2 Out of scope for this draft

**No global binding of `my_id`.**
This draft does not bind `my_id` to a person or organization beyond local files. It defines no registry or directory service.

**Limited clock handling.**
Only timestamp parsing is performed. Applications may add tighter acceptance windows or rely on secure time sources.

**Key rotation and revocation.**
Rotation and revocation flows are not specified. They can be introduced later without changing the tuple `(my_id, sign_pub, kx_pub)`.

**No transcript or channel binding beyond the handshake tuple.**
If stronger linkage to transport parameters is required, applications must add it explicitly.



### 7.3 At-rest protection

**Mechanism.**
The identity file is sealed with [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) under a key derived by scrypt. Only metadata and ciphertext are stored. Raw private keys never appear unencrypted on disk. The container records the KDF parameters, the salt, the nonce, and a fixed associated-data string so that accidental cross-context decryption attempts are rejected.

**Operational expectations.**
The passphrase should come from a secure source. File permissions should restrict access to the owning user. Backups of the identity file and backups of the passphrase should be stored separately.

**Failure behavior.**
If decryption fails, the version tag is unknown, or the KDF tag is unexpected, the agent refuses to start. This prevents running with partially parsed or downgraded identity material.



### 7.4 In-memory handling

**Scope of secrets in RAM.**
The agent holds the long-term private keys and the per-pair session keys in memory. Session keys and peer signing keys are cached in maps keyed by `(role, peer_id)` so handlers can verify envelopes and signatures efficiently.

**Lifetime.**
Session keys are computed when the signed handshake is validated. They remain in memory for the duration of the run. The agent does not persist session keys to disk.

**Process exit.**
Memory is released on termination. This draft does not include explicit zeroization. If zeroization is required by policy, it should be added at well-defined shutdown points that cover all key copies.



### 7.5 Network threat scenarios and mitigations

**Impersonation at the start of a cycle.**
An adversary would need to produce a valid [Ed25519](https://ed25519.cr.yp.to/) signature over the tuple that binds the expected nonce. Without the private signing key this is infeasible.

**Message tampering in transit.**
When envelopes are enabled, tampering is detected because the JSON envelope is signed and the ciphertext is authenticated by [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode). When envelopes are not used, tampering is still detectable at the handshake boundary but not for plaintext payloads.

**Replay of early messages.**
The nonce echo and the per-pair `NonceEvent` log block trivial replays. A repeated handshake nonce within the TTL is rejected. Repeats of exchange nonces are logged and ignored.

**Reflection and cross-peer confusion.**
All state and nonce logs are scoped by `(self_id, role, peer_id)`. A message replayed from a different peer identifier will not satisfy the expected state or nonce checks.

**Downgrade to unauthenticated exchange.**
The state machine requires a valid handshake to begin a cycle. If a peer omits the handshake, the receiver does not populate a session key and continues as plaintext in line with this demo's permissive behavior. Deployments that require sealed payloads can enforce a policy that rejects plaintext whenever a key is present.



### 7.6 Replay accounting and database considerations

**Handshake window.**
The handshake validator uses `DBNonceStore` to decide whether a nonce has already been seen and whether it has expired. The default TTL is sixty seconds.

**Exchange loop.**
Each received `my_nonce` is recorded exactly once. If a duplicate arrives, it is recorded as a duplicate event and ignored by the state machine.

**Database integrity.**
If `NonceEvent` entries are lost or corrupted, replay resistance falls back to nonce-echo checks only. Environments that treat replay resistance as critical should use durable storage and run integrity checks on the database.

**Cleanup.**
On a successful close, nonce rows for that peer and role are deleted. This resets transient history so the next cycle starts cleanly.



### 7.7 Forward secrecy and key-reuse trade-offs

The current demo keeps long-term [X25519](https://en.wikipedia.org/wiki/Curve25519) keys and uses [HKDF](https://en.wikipedia.org/wiki/HKDF) without salt. For a given pair of long-term keys, the derived value is stable across cycles. If a long-term X25519 private key is later disclosed, previously captured encrypted envelopes can be decrypted because the same derived key was used. This limitation is accepted for a state-and-orchestration demo and is stated explicitly so operators can adopt an appropriate posture.

Two hardening options remain compatible with this design. One option is to use an ephemeral X25519 key for each cycle while keeping the [Ed25519](https://ed25519.cr.yp.to/) signing key long-term. This preserves recognizability through signatures and yields a new [ECDH](https://en.wikipedia.org/wiki/Elliptic-curve_Diffie%E2%80%93Hellman) input for HKDF every cycle. Another option is to keep long-term X25519 keys but add a per-cycle HKDF salt that both sides can recompute from already validated freshness material, such as a function of both nonces and the handshake timestamp. Either approach changes only the derivation inputs and does not alter message formats or storage semantics.



### 7.8 Denial-of-service considerations

The receive hooks and handlers validate inputs and check state before more expensive work. Handshake validation performs signature verification and a database read. These operations are bounded, but they are not free. Deployments exposed to untrusted networks should rate-limit at the transport layer and cap in-flight work per peer. Because handshake nonces are short-lived, a burst of invalid attempts does not pollute long-term state beyond `NonceEvent` inserts, which are already scoped by peer and role. Log levels should be tuned so that excessive output does not become a resource concern.



### 7.9 Configuration and operational posture

The identity file and the database are the primary assets. Each process should use a unique `--name` so identities are not unintentionally shared. The encrypted identity file and the passphrase should be stored separately. Secrets must not be written to logs. The code attempts to set restrictive file permissions, and deployments should confirm those permissions or apply equivalent controls on platforms that do not support Unix modes.



### 7.10 Residual risks and recommended hardening

Residual risks include repeatable derived keys for a given long-term pair, plaintext operation when envelopes are not enforced by policy, and dependence on local database integrity for replay resistance beyond the echo check. Recommended hardening measures include adopting ephemeral [X25519](https://en.wikipedia.org/wiki/Curve25519) keys or a salted [HKDF](https://en.wikipedia.org/wiki/HKDF) per cycle, enforcing a policy that requires envelopes whenever a session key exists, verifying database integrity on a schedule, and optionally zeroizing key material at shutdown. All of these measures remain within the specification boundaries of this draft and do not rely on external frameworks.


## 8. Multi-peer behavior

A single process may talk to many peers. All invariants are enforced per `(self_id, role, peer_id)` tuple. Session keys, signing keys, nonces, and references are independent per peer.



## 9. Practical guidance

* Use a unique `--name` per process. Reusing names shares identity and database files and may cause confusing state transitions.
* If a name was reused, stop agents and remove the matching identity and database files or start with a new name.
* Keep `PERSIST_CRYPTO` enabled. It is safe when optional columns are present. It is a no-op otherwise.



## 10. Compatibility notes and taxonomy mapping

**Scope of comparison.** The following is a terminology bridge for readers familiar with blockchain and W3C-style DID vocabularies. It is descriptive only. Summoner DIDs are defined by this document and do not adopt an external DID method or registry.

**Identifier.**

* **Summoner:** `my_id` is a local subject identifier used for routing and labeling.
* **W3C DID analogy:** similar to a DID subject identifier, but **not** bound to a DID method string and **not** resolved through a global registry.

**Verification methods.**

* **Authentication:** `sign_pub` is an [Ed25519](https://ed25519.cr.yp.to/) verification key used to validate agent-produced signatures. In DID terms, this aligns with an *authentication* verification method.
* **Key agreement:** `kx_pub` is an [X25519](https://en.wikipedia.org/wiki/Curve25519) public key used to derive symmetric session keys via [ECDH](https://en.wikipedia.org/wiki/Elliptic-curve_Diffie%E2%80%93Hellman) and [HKDF](https://en.wikipedia.org/wiki/HKDF). In DID terms, this aligns with a *key agreement* verification method.

**Service endpoints.**

* **Summoner:** not defined in this draft. Endpoints are configured out of band by the Summoner client and server.
* **DID analogy:** comparable to omitting `service` entries in a DID document.

**Method operations.**

* **Create / load:** local file operations over an encrypted identity file (`id_agent_<name>.json`).
* **Resolve / update / deactivate:** not specified. There is no global resolution or on-chain anchoring in this draft.

**Interoperability posture.**
External tooling may treat the tuple `(my_id, sign_pub, kx_pub)` as the minimal record when a DID-like document is required for integration. Any such mapping should be clearly labeled as a compatibility layer so that Summoner semantics (local resolution, file-scoped lifecycle, and out-of-band service configuration) remain unchanged.


## 11. Future work

**Key rotation with continuity proofs.**
Define a rotation flow that replaces one or both long-term keys without breaking the application’s notion of "the same agent." The draft direction is to publish a signed *continuity proof* where the current `sign_priv` signs a statement that names the new public keys (and, optionally, the old ones), a rotation reason, and a timestamp. Peers that already trust the old key can verify the signature and accept the new key material without out-of-band coordination. 

> [!NOTE]
> The specification should cover: how rotations are recorded in the encrypted identity file, how peers cache and expire prior keys, and how to handle recovery if a rotation only partially propagates.

**Optional transcript or channel binding.**
Introduce an opt-in field that binds the handshake to selected transport parameters—for example, a canonical string that includes the peer addresses observed by each side, or a hash of the first application payload. The goal is to make cross-channel replay harder and to give operators a clear lever when they want stronger linkage to the transport. 

> [!NOTE]
> The draft should spell out what is bound, how it is encoded, and how strict the verifier should be in the presence of NATs, proxies, or load balancers.

**Ephemeral X25519 while keeping long-term signing keys.**
Add a mode where each cycle uses a short-lived [X25519](https://en.wikipedia.org/wiki/Curve25519) key (ephemeral per cycle) authenticated by the long-term Ed25519 signature. This yields a fresh ECDH input for HKDF every time while preserving recognizability through the signing key. 

> [!NOTE]
> The specification should define how the ephemeral public key is conveyed (most naturally inside the existing `hs`), how peers prove it belongs to the signer, and how lifetimes and reuse are enforced to avoid accidental key recycling.

**Reconnect semantics and session resumption.**
Formalize reconnect behavior using explicit resumption tokens derived from already authenticated material (for example, a function of both references and the validated handshake timestamp). The aim is to let a peer resume quickly after transient failures without redoing a full exchange, while preserving replay resistance and the finalize rules. 

> [!NOTE]
> The draft should cover token lifetime, uniqueness per `(role, peer_id)`, storage considerations, and how resumption interacts with policy choices such as "envelopes required."

**Operational policy hooks.**
Expose configuration switches that tighten posture without altering wire formats. Examples include "require envelopes whenever a session key exists," "refuse plaintext on reconnect," and adjustable handshake TTLs. 

> [!NOTE]
> The specification should document expected failure modes and recommended defaults.

**Observability and audits.**
Specify minimal, structured log fields for security-relevant events (handshake accept/reject reasons, replay decisions, envelope verification outcomes) so operators can audit behavior without logging secrets. 

> [!NOTE]
> Include guidance for redaction and log retention.


<hr>
<p align="center" style="text-align:center; letter-spacing:0.5em;">
  · · ·
<hr>
<br><br>

# Appendix


## 12. Appendix A: Reference algorithms and function mapping

<details>
<summary>
<b>Purpose and use.</b> This appendix is a code-to-spec index. Each entry states <em>what the function does, what it consumes, what it returns,</em> and <em>what to verify</em> when testing. It is intentionally implementation-facing and maps directly to the reviewed files.
</summary>

### 12.1 Identity persistence

<details><summary>
<code><b>save_identity_json_encrypted(path, password, my_id, kx_priv, sign_priv)</code></b>
</summary>

* **Role:** Persist DID material to disk with at-rest confidentiality.
* **Inputs:** filesystem path; passphrase; the agent identifier and two private keys.
* **Output:** Encrypted JSON identity file.
* **Side effects:** Writes file (best-effort `chmod 600`).
* **Security notes.** The identity file's encryption key is derived with scrypt (`n = 2^14`, `r = 8`, `p = 1`, `length = 32`). The file is then sealed using AES-GCM with a 12-byte nonce, and the operation includes the fixed associated data string `HSAgent.identity.v1`. At no point are raw private keys written to disk in unencrypted form.


</details>

<details><summary>
<code><b>load_identity_json_encrypted(path, password)</code></b>
</summary>

* **Role:** Recover DID material into memory for runtime use.
* **Inputs:** path; passphrase.
* **Returns:** `(my_id, kx_priv, sign_priv, kx_pub_b64, sign_pub_b64)`
* **Side effects:** None beyond memory allocation.
* **Verify:** Wrong password, salt, nonce, or AAD must fail closed; successful decrypt reproduces the tuple exactly.

</details>

### 12.2 Handshake construction and validation

<details><summary>
<code><b>build_handshake_message(msg_type, nonce, priv_kx, priv_sign)</code></b>
</summary>

* **Role:** Produce the signed `hs` blob attached to the first authenticated message in a cycle.
* **Inputs:** `"init"` or `"response"`; echo-target nonce; [X25519](https://en.wikipedia.org/wiki/Curve25519) private key; [Ed25519](https://ed25519.cr.yp.to/) private key.
* **Returns:**

  ```json
  {
    "type": "init|response",
    "nonce": "<echo target>",
    "kx_pub": "<b64>",
    "sign_pub": "<b64>",
    "timestamp": "<ISO8601>",
    "sig": "<b64 Ed25519 over 'nonce|kx_pub|timestamp'>"
  }
  ```
* **Verify:** Signature must verify under `sign_pub`; timestamp must parse; fields are present and non-empty.

</details>

<details><summary>
<code><b>validate_handshake_message(msg, expected_type, expected_nonce, nonce_store, priv_kx)</code></b>
</summary>

* **Role:** Authenticate the peer's first signed message and derive the symmetric key.
* **Inputs:** received `hs`; expected `"init"`/`"response"`; expected echo nonce; TTL-enforcing `nonce_store`; local X25519 private key.
* **Returns:** 32-byte `sym_key` via X25519 + HKDF-SHA256 (`info="handshake"`).
* **Side effects:** Records the handshake nonce as *received*; rejects type/nonce/timestamp/signature errors; rejects replays/staleness per TTL.
* **Verify:** Duplicate nonce within TTL is rejected; mismatched type/nonce rejected; tampered signature rejected.

</details>

### 12.3 Secure envelope

<details><summary>
<code><b>seal_envelope(sym_key, sign_priv, obj)</code></b>
</summary>

* **Role:** Provide confidentiality and integrity for payloads.
* **Inputs:** 32-byte session key; [Ed25519](https://ed25519.cr.yp.to/) signing key; JSON-serializable object (e.g., `{"message": "..."}`).
* **Returns:**

  ```json
  {
    "envelope": {
      "nonce": "<b64 12B>",
      "ciphertext": "<b64>",
      "hash": "<b64 sha256(plaintext)>",
      "ts": "<ISO8601>"
    },
    "sig": "<b64 [Ed25519](https://ed25519.cr.yp.to/) over JSON(envelope)>"
  }
  ```
* **Verify:** Canonical JSON (sorted keys) before hashing; fresh 12-byte nonce each call.

</details>

<details><summary>
<code><b>open_envelope(sym_key, peer_sign_pub_b64, signed)</code></b>
</summary>

* **Role:** Verify the envelope signature and decrypt the ciphertext.
* **Inputs:** session key; peer [Ed25519](https://ed25519.cr.yp.to/) public key; the signed envelope.
* **Returns:** Decoded JSON plaintext (e.g., `{"message": "..."}`).
* **Verify:** Any change to `envelope` breaks the signature; AES-GCM failures are surfaced; recomputed SHA-256 must match `hash`.

</details>

### 12.4 Replay window and nonce tracking

<details><summary>
<code><b>DBNonceStore(self_id, role, peer_id, ttl_seconds=60)</code></b>
</summary>

* **Role:** Back `exists / is_expired / add` for handshake replay defense with a TTL window.
* **Back-end:** `NonceEvent(self_id, role, peer_id, flow, nonce)` with indexes for dedupe and cleanup.
* **Verify:** First observation inserts a `received` record; duplicates within TTL are rejected; expiry restores eligibility for a *new* handshake with a fresh nonce.

</details>


### 12.5 End-to-end verification checklist

* Identity file encrypts/decrypts under the correct password; incorrect parameters fail closed.
* Handshake validates type/nonce/timestamp/signature and yields a consistent 32-byte key for a given peer key pair.
* Secure envelope round-trip preserves payload; any tampering is detected.
* Replay attempts using the same `hs.nonce` inside TTL are rejected; `NonceEvent` entries are cleared on successful `close` per peer.

</details>

## 13. Appendix B: Example objects

<details>
<summary>
<b>Purpose and use.</b> These wire-level examples illustrate the serialized shapes that correspond to the algorithms above. They are inspection aids for logging and tests—not templates to hard-code.
</summary>

### 13.1 Example handshake (init)

What it is: a first-message `hs(type="init")` from an initiator.
How to read: `nonce` is the echo target; `kx_pub` and `sign_pub` advertise keys; `sig` authenticates the tuple `nonce|kx_pub|timestamp`.

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

**Quick checks:** Flip any byte in `kx_pub` or `nonce` → validation fails; resubmitting the same `nonce` within the TTL → replay rejection.



### 13.2 Example secure envelope

What it is: a sealed payload replacing plaintext `message`.
How to read: `nonce` is the AES-GCM nonce; `hash` is SHA-256 over canonical plaintext; `sig` covers the entire `envelope`.

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

**Quick checks:** Any change to `envelope` invalidates `sig`; altering `ciphertext` breaks decryption; altering `hash` trips the post-decrypt fingerprint check.



### 13.3 Example decrypted payload

What it is: the object returned by `open_envelope(...)` when verification and decryption succeed.

```json
{ "message": "How are you?" }
```

**Quick checks:** The agent surfaces this at `content["message"]`; if the peer's `sign_pub` is unknown/mismatched, signature verification fails and plaintext is not surfaced.

</details>

## 14. Appendix C: Compliance checklist for implementations

<details><summary>
<b>Purpose.</b> This checklist enumerates the concrete requirements that an implementation must satisfy, together with suggested verification steps and failure handling. It is written to be used during code review and test execution.
</summary>

### 14.1 Identity persistence

<details><summary>
<b>Requirement:</b> The identity file uses scrypt for key derivation and AES-GCM for at-rest encryption. Private keys never appear unencrypted on disk.
</summary>
<br>

* **Parameters:** `scrypt(n=2^14, r=8, p=1, length=32)`, AES-GCM with a random 12-byte nonce, AAD equal to the literal `HSAgent.identity.v1`.
* **File format:**

  * Outer container:

    ```json
    { "v":"id.v1","kdf":"scrypt","salt":"<b64>","nonce":"<b64 12B>","aad":"<b64 HSAgent.identity.v1>","ciphertext":"<b64>" }
    ```
  * Decrypted payload:

    ```json
    { "my_id":"<uuid>","created_at":"<ISO8601 UTC>","kx_priv_b64":"<b64 32B>","kx_pub_b64":"<b64 32B>","sign_priv_b64":"<b64 32B>","sign_pub_b64":"<b64 32B>" }
    ```
</details>


* **How to verify:**

  1. Create an identity, then scan the file. No unencrypted private key material must appear.
  2. Decrypt with the correct passphrase and recover the exact tuple `(my_id, kx_priv, sign_priv, kx_pub_b64, sign_pub_b64)`.
  3. Attempt decryption with a wrong passphrase, wrong AAD, or modified nonce. The operation must fail closed.
  4. Check file permissions are set to owner read and write only if supported by the platform (`chmod 600` best effort).

**Failure handling:** Refuse to start if the file version is unknown, the KDF tag is not `scrypt`, or decryption fails.



### 14.2 Handshake structure and authentication

<details><summary>
<b>Requirement:</b> The first authenticated message in each cycle includes a signed handshake that echoes the expected nonce.
</summary>
<br>

* **Fields:**

  ```json
  {
    "type":"init|response",
    "nonce":"<echo target>",
    "kx_pub":"<b64>",
    "sign_pub":"<b64>",
    "timestamp":"<ISO8601>",
    "sig":"<b64 [Ed25519](https://ed25519.cr.yp.to/) over 'nonce|kx_pub|timestamp'>"
  }
  ```
* **Validation rules:**

  1. `type` equals the expected direction.
  2. `timestamp` parses.
  3. `nonce` equals the expected echo target.
  4. Signature verifies under `sign_pub`.
  5. Replay and staleness are checked via the nonce store with a TTL window.

</details>

**How to verify:**

* Corrupt any of the fields and confirm `validate_handshake_message(...)` rejects the message for the appropriate reason.
* Reuse the same handshake nonce within the TTL and confirm replay rejection.

**Failure handling:** Do not update state, do not derive a session key, and continue waiting for a valid handshake.



### 14.3 Session key derivation

<details><summary>
<b>Requirement:</b> The session key is derived using X25519 and HKDF-SHA256 with <code>info="handshake"</code> and <code>salt=None</code>, yielding exactly 32 bytes.
</summary>
<br>

* **Derivation:** `sym_key = HKDF_SHA256(X25519(priv_kx, peer_kx_pub), info="handshake", salt=None, length=32)`.
* **Scope:** Store the derived key only in memory, keyed by `(role, peer_id)`. Do not persist to disk.

</details>

**How to verify:**

* For a fixed pair of long-term X25519 keys, the derived key must be stable across repeated derivations.
* A single bit change in either private key or peer public key must change the derived key.
* The derived key length is exactly 32 bytes.

**Failure handling:** If derivation or input parsing fails, abort the handshake and leave no partial key material in memory.

**Note on security posture:** With static X25519 keys and no salt, the derived key will repeat for a given pair. This is acceptable for the demo. If forward secrecy per cycle is required, see [§3.5](#35-key-lifecycle-and-forward-secrecy-considerations) for hardening options that remain within the current design.



### 14.4 Replay defense and nonce accounting

<details><summary>
<b>Requirement:</b> Replay defense uses a per-pair <code>NonceEvent</code> table and a TTL window for handshake nonces.
</summary>
<br>

* **Data model:** `NonceEvent(self_id, role, peer_id, flow, nonce)` with indexes for `(self_id, role, peer_id)` and `(self_id, role, peer_id, flow, nonce)`.
* **Flows:**

  * On send, record `flow="sent"` for the locally emitted nonce.
  * On receive, record `flow="received"` once per unique nonce.
  * The handshake validator consults `DBNonceStore.exists(...)` and `is_expired(...)` to reject duplicates or stale handshakes.

</details>

**How to verify:**

* Receiving the same `my_nonce` twice results in a single `received` record and the duplicate message is ignored.
* A handshake with a previously seen nonce is rejected while within TTL.
* After a successful `close`, all nonce rows for that `(role, peer_id)` are removed.

**Failure handling:** Ignore messages that fail nonce checks. Do not mutate state beyond logging.



### 14.5 Secure envelope

<details><summary>
<b>Requirement:</b> When used, the secure envelope provides confidentiality and integrity for application payloads.
</summary>
<br>

* **Seal:**

  1. Canonicalize the payload JSON with sorted keys.
  2. Compute `hash = SHA256(plaintext)`.
  3. Encrypt with [AES-GCM](https://en.wikipedia.org/wiki/Galois/Counter_Mode) using a fresh 12-byte nonce and `associated_data = hash`.
  4. Sign `JSON(envelope)` with Ed25519.
* **Open:**

  1. Verify signature under the peer's `sign_pub`.
  2. Decrypt with AES-GCM using the embedded nonce and `associated_data = hash`.
  3. Recompute SHA-256 and match against `hash`.
  4. Return the decoded object. If it contains `{"message": ...}` surface it as `content["message"]`.

</details>

**How to verify:**

* Tamper any field inside `envelope` and confirm signature verification fails.
* Tamper `ciphertext` and confirm AES-GCM decryption fails.
* Tamper `hash` and confirm the post-decrypt fingerprint check fails.
* Confirm that a fresh AES-GCM nonce is generated for every sealed envelope.

**Failure handling:** On any verification or decryption error, do not surface plaintext. Log and continue processing the outer message as if `sec` were absent.



### 14.6 Finalization and cleanup

<details><summary>
<b>Requirement:</b> On a valid <code>close</code>, the nonce log is deleted for the pair and transient counters are reset according to the route logic.
</summary>
<br>

* **Initiator path:** `conclude(my_ref)` then accept `finish(your_ref, my_ref)` then send `close(your_ref, my_ref)`. On success, clear `NonceEvent` rows for `(self_id, role="initiator", peer_id)`.
* **Responder path:** After sending `finish`, accept a valid `close` and clear `NonceEvent` rows for `(self_id, role="responder", peer_id)`.

</details>

**How to verify:**

* After a successful close, queries for `NonceEvent` with the pair keys return no rows.
* Reconnect semantics continue to operate with preserved references where defined by the state machine.

**Failure handling:** If finalize retries exceed configured limits, reset counters to avoid deadlock while preserving references as specified by the route logic.



### 14.7 Operational controls

<details><summary>
<b>Requirement:</b> Operational posture matches the code's assumptions.
</summary>
<br>

* Unique `--name` per process to avoid unintended identity sharing.
* Identity file backups are stored separately from passphrases.
* Logs never include private key material. Short digests of derived keys are acceptable for debugging.
* File permissions are restricted where the platform allows it.

</details>

**How to verify:**

* Attempt to start two processes with the same `--name` and confirm they share identity and DB. This is expected but must be documented and avoided in production.
* Audit logs for absence of raw key bytes.
* Check file permission modes on Unix-like systems.



### 14.8 Documentation and test artifacts

<details><summary>
<b>Requirement:</b> Ship evidence that each checklist item was exercised.
</summary>
<br>

* Unit tests for identity decrypt failure modes.
* Property tests for handshake validation under random tampering.
* Round-trip tests for `seal_envelope` and `open_envelope`.
* Replay tests that confirm TTL behavior and per-pair cleanup on `close`.

</details>

**Outcome:** A reviewer can trace each requirement in this section to a test or manual step and confirm expected pass and fail behaviors without ambiguity.


</details>

