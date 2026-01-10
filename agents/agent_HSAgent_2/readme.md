# HSAgent_2: A Cloud-Native Cryptographic Handshake Agent

**Docker build**

`docker build -t hsagent-2 .`

**Docker run**

(in `summoner-agents`) `python server.py`

`docker run --rm --network="host" hsagent-2 --name agent-alpha`
`docker run --rm --network="host" hsagent-2 --name agent-beta`

## Overview

HSAgent_2 is a multi-peer, cloud-native agent designed to establish secure, authenticated communication channels between independent services in a distributed system. By performing a sophisticated cryptographic handshake, it ensures that when one service communicates with another, the channel is private, authenticated, and resistant to tampering.

This agent is a significant evolution of its predecessors (HSAgent_0, HSAgent_1). It migrates all state management from local SQLite databases to a persistent, cloud-based "substrate" built on scalable object storage (BOSS) and append-only ledger (Fathom) services. This migration exposes and corrects several critical race conditions and design flaws, resulting in a protocol that is robust, scalable, and suitable for high-concurrency cloud environments.

## Architectural Model: Stateless Discovery

A core design decision in HSAgent_2 is its adherence to the **Stateless Discovery Model**. This model prioritizes simplicity and robustness by treating every communication session as an atomic, independent event.

### The Evolution from Stateful Reconnection

This is a deliberate change from the "Stateful Reconnection" model attempted in HSAgent_1. While the original intent was to allow for efficient reconnection after transient failures, its implementation contained a critical flaw: it could not distinguish between a clean shutdown and an unexpected crash. This led to an infinite loop of successful handshakes followed by immediate reconnection attempts.

### The Stateless Solution

The Stateless Discovery model solves this definitively:

**Full State Purge:** When a session ends—for any reason—both agents completely purge all state associated with that session (nonces, references, etc.).

**Discovery via Broadcast:** Agents discover each other through periodic broadcast register messages.

**Fresh Handshake:** Every new interaction between two agents begins with a full, fresh cryptographic handshake, as if they were total strangers.

### Advantages for Distributed Systems

**High Robustness:** Eliminates the risk of stale state from a previous run corrupting a new session. This is critical in a cloud environment where agents may be restarted or scaled independently.

**Simplicity:** The logic is significantly easier to debug and reason about, reducing the surface area for complex bugs.

**Security:** Forcing a new key exchange for each session enhances security by ensuring perfect forward secrecy.

While this model incurs the computational cost of a key exchange for every session, its guarantees of stability and correctness are paramount for any security-sensitive task in a distributed environment.

## Protocol Flow and State Machine

The agent's logic is governed by a state machine that ensures two agents can discover each other, securely exchange keys, conduct a brief message exchange, and cleanly terminate the session.

### Roles and States

**Roles:** Initiator and Responder

**States:**
- **Initiator:** `init_ready` → `init_exchange` → `init_finalize_propose` → `init_finalize_close` → `init_ready`
- **Responder:** `resp_ready` → `resp_confirm` → `resp_exchange` → `resp_finalize` → `resp_ready`

### Key Protocol Stages

#### Stage 1: Discovery and Role Assignment

**Broadcast:** All idle agents broadcast a `register` message every few seconds to announce their presence.

**Symmetry-Breaking:** When two agents receive each other's `register` message, they apply a deterministic rule: the agent with the lexicographically greater agent ID becomes the Responder. The other becomes the Initiator and ignores the register message. This critical step prevents deadlocks.

#### Stage 2: Cryptographic Handshake

1. The Responder moves to the `resp_confirm` state and sends a `confirm` message containing a signed handshake blob (`hs`).

2. The Initiator receives the `confirm`, validates it, performs an X25519 key exchange, and derives a shared symmetric session key.

3. The Initiator sends its first `request`, which also contains its signed `hs` blob.

4. The Responder validates the Initiator's handshake and derives the same shared key. The secure channel is now established.

#### Stage 3: Message Exchange

- The agents exchange a series of `request` and `respond` messages.
- Each message contains a nonce that must be echoed by the counterpart, ensuring messages are in sequence.
- Optionally, the message payload can be encrypted using the derived session key and placed in a secure envelope (`sec`).

#### Stage 4: Clean Session Finalization

1. After `EXCHANGE_LIMIT` messages, the Initiator sends a `conclude` message with a session reference.

2. The Responder replies with a `finish` message containing its own reference.

3. The Initiator replies with a final `close` message containing both references.

4. Upon successful validation of the `close` message, the Responder deletes all state for the session.

5. After sending `close` for a few ticks (`FINAL_LIMIT`), the Initiator times out and also deletes all state for the session.

This clean, two-sided teardown prevents infinite loops.

## Key Components and Modules

### Core Agent Logic

#### `agent.py`
The main entrypoint. Contains the agent's state machine logic, message handlers (`@client.receive`), the send driver (`@client.send`), and all core protocol orchestration.

### Backend Integration

#### `adapt.py`
A high-level, asynchronous client library for interacting with the project's backend services. It handles authentication, automatic token renewal, and provides a clean interface to the underlying API.

#### `api.py`
Defines the data models and high-level adapters for the backend.

### State Management

#### `SubstrateStateStore`
A cloud-native state manager that persists `HandshakeState` objects, handling creation, atomic updates, and queries.

#### `HybridNonceStore`
A high-performance nonce tracker that uses fast lookups for validation and an append-only ledger for an immutable audit log, providing robust replay protection.

### Cryptographic Operations

#### `crypto_utils.py`
A self-contained library for all cryptographic operations: key generation, serialization, signing (Ed25519), key exchange (X25519), key derivation (HKDF), and AEAD encryption (AES-GCM).

### System Validation

#### `selftest.py`
A critical pre-flight check that runs on agent startup. It provisions a temporary user and runs a suite of live-fire tests against the backend services to validate that all components are functioning correctly before the main agent logic starts.

## Technical Architecture

### Cloud-Native Design

HSAgent_2 represents a fundamental shift from local state management to cloud-native persistence. By leveraging the BOSS and Fathom services, the agent achieves:

- **Scalability:** Multiple agent instances can operate independently without state conflicts
- **Persistence:** Session state survives agent restarts and deployments
- **Auditability:** All cryptographic operations are logged to an immutable ledger
- **Reliability:** Atomic state updates prevent corruption during concurrent operations

### Security Model

The agent implements a defense-in-depth security architecture:

- **Perfect Forward Secrecy:** Each session uses ephemeral keys that cannot compromise past or future sessions
- **Mutual Authentication:** Both parties must prove their identity through digital signatures
- **Replay Protection:** Nonces and timestamps prevent message replay attacks
- **Tamper Resistance:** All messages are cryptographically signed and optionally encrypted

### Operational Excellence

#### Pre-Flight Testing Philosophy

The `selftest.py` module embodies the platform's "blood on the game-ball" philosophy. Before accepting any real traffic, the agent:

1. Provisions temporary test credentials
2. Exercises all backend APIs with real transactions
3. Validates cryptographic operations end-to-end
4. Confirms all state storage mechanisms are functional
5. Only proceeds if all tests pass with flying colors

This approach ensures that failures are discovered during startup rather than during critical operations, providing the confidence that comes from battle-tested systems.

## Conclusion

HSAgent_2 represents the maturation of the handshake protocol from a proof-of-concept to a production-ready foundation for secure service-to-service communication in distributed systems. Through its stateless design, comprehensive security model, and rigorous testing philosophy, it provides the reliable trust layer that modern cloud-native applications demand.