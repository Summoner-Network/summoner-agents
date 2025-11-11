# `HSAgent_1`

A multi-peer **handshake** agent that keeps the [`HSAgent_0`](../agent_HSAgent_0/) flow and state model but adds end-to-end cryptography and key protection. It **signs the handshake** (`hs`, Ed25519), performs an **ephemeral X25519** exchange + HKDF to derive a **symmetric session key**, can wrap payloads in **secure envelopes** (`sec`, AES-GCM with an Ed25519 envelope signature), and persists per-agent keys in an **encrypted identity file** (`id_agent_<name>.json`). All **client-side** Summoner SDK routes and the ORM-backed DB state remain the same as `HSAgent_0` (with optional crypto metadata columns on [`RoleState`](./db_models.py)).

> [!NOTE]
> This is an orchestration/state demo with a strong crypto veneer; key management is simplified. The identity file is **encrypted at rest**: private keys are sealed with **AES-GCM** using a key derived from a passphrase via **scrypt** ($N=2^{14}$, $r=8$, $p=1$). The JSON on disk contains only version/KDF metadata, salt, nonce, and ciphertext — **never** raw private key bytes. For real deployments, supply a strong passphrase (env var, OS keychain, or KMS), not the demo default.
>
> **Heads-up (where things live):**
> - **Identity:** long-term, per-agent; stored encrypted on disk.  
> - **Session key & peer sign pub:** per *(role, peer)*; derived on first authenticated message; **RAM-only** for the session (peer pub may also be persisted best-effort).  
> - **Nonces:** per *(role, peer)*; all received nonces are recorded **once** for replay defense; cleared on successful `close`.

This agent relies on two supporting files:

* [`crypto_utils.py`](./crypto_utils.py) — handshake signing/verification, session key derivation, secure envelope seal/open, encrypted identity save/load
* [`db_models.py`](./db_models.py) — same tables as `HSAgent_0`, plus **optional** crypto metadata fields on `RoleState`

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

The route/state machine is unchanged from `HSAgent_0`:

* **Initiator:** `init_ready → init_exchange → init_finalize_propose → init_finalize_close → init_ready`
* **Responder:** `resp_ready → resp_confirm → resp_exchange → resp_finalize → resp_ready`

> 📝 **Note (storage & invariants):**
> - **Identity (disk):** `my_id`, `kx_priv`(X25519), `sign_priv`(Ed25519) are created on first run for a `--name`; delete the file to force regeneration (demo only).  
> - **Session key (RAM):** `SYM_KEYS[(role, peer_id)]` (32-byte X25519+HKDF) is set **when validating the peer's signed `hs`**:
>   - Initiator learns it on **inbound** `confirm` with `hs(type="response")`.
>   - Responder learns it on **inbound** `request` with `hs(type="init")`.
>   Used to seal `message → sec` on send and to open `sec` on receive. Re-derived on the next handshake cycle.  
> - **Peer sign pub (RAM + optional DB):** `PEER_SIGN_PUB[(role, peer_id)]` captured during `hs` validation; optionally persisted (`PERSIST_CRYPTO=True`) to `RoleState.peer_sign_pub` (+ `peer_kx_pub`, `hs_derived_at`, `last_secure_at`).  
> - **Nonces (DB):** both **exchange** and **handshake** nonces are logged in `NonceEvent(self_id, role, peer_id, flow, nonce)`.  
>   - Send path: append `flow="sent"` for each locally emitted nonce.  
>   - Receive path: record once via `record_received_nonce_once(...)`; duplicate `my_nonce` with `flow="received"` → message ignored.  
>   - Handshake `hs.nonce` is replay-checked via `DBNonceStore` with a **60s TTL**.  
>   - **Clear:** all nonce rows for the pair are deleted after a successful `close`.  
> - **Echo rule:** every `request/respond` must satisfy `your_nonce == last counterpart local_nonce`.  
> - **Finalize rule:** `conclude(my_ref) → finish(your_ref,my_ref) → close(your_ref,my_ref)` must match.
> - **Peer scoping (upload/download):** `upload_states()` advertises keys **per peer** as `"initiator:<peer_id>"` / `"responder:<peer_id>"`. `download_states()` splits that compound key so we update exactly the `(self_id, role, peer_id)` row—avoids global, cross-peer state jumps.
> - **NonceEvent indexes (replay/cleanup):** `NonceEvent(self_id, role, peer_id, flow, nonce)` is indexed by `(self_id, role, peer_id)` and `(self_id, role, peer_id, flow, nonce)`. This supports fast dedupe checks and cheap per-pair deletes on `close`.


### What's added

1. **Signed handshake (`hs`)**

   * On the **first** `request` (initiator) and on `confirm` (responder), agents attach:

     ```
     {
       "type": "init" | "response",
       "nonce": <echo target>,
       "kx_pub": <base64>,
       "sign_pub": <base64>,
       "timestamp": <ISO8601>,
       "sig": <Ed25519 over "nonce|kx_pub|timestamp">
     }
     ```
   * The receiver validates `hs`, probes replay/TTL via the DB-backed store, and derives a **32-byte session key** via X25519+HKDF.

2. **Secure envelope (`sec`)** *(optional)*

   * Once a session key exists, plain `"message"` may be replaced with:

     ```
     "sec": {
       "envelope": {
         "nonce": <b64 12B>,
         "ciphertext": <b64>,
         "hash": <b64 sha256(plaintext)>,
         "ts": <ISO8601>
       },
       "sig": <Ed25519 over JSON(envelope)>
     }
     ```
   * The receiver verifies signature, decrypts (AES-GCM), checks the hash, and surfaces the plaintext as `content["message"]`.

3. **Identity persistence**

   * Each agent loads/saves `id_agent_<name>.json` (AES-GCM sealed with a password-derived key via scrypt).
   * File contains `my_id` + private/public keys (X25519/Ed25519).

### Receive routes (selected deltas)

* **Responder**

  * `resp_confirm → resp_exchange`  
    Validates the first `"request"`. If `hs` is present/valid, derives `SYM_KEYS[("responder", peer_id)]`, sets `PEER_SIGN_PUB`, and records the peer's `my_nonce` **once**.
  * `resp_exchange → resp_finalize`  
    Continues ping-pong or accepts `"conclude"`. If `sec` is present and keys are known, decrypts to `message` and logs `last_secure_at`.

* **Initiator**

  * `init_ready → init_exchange`  
    On `"confirm"`, if `hs` present/valid, derives `SYM_KEYS[("initiator", peer_id)]`, sets `PEER_SIGN_PUB`, and records the peer's `my_nonce` **once**.
  * `init_exchange → init_finalize_propose`  
    Normal echo checks; if `sec` is present, opens it to `message`.

### Send driver (per role & peer)

* **Initiator**
  * `init_exchange`: emits `"request"`. On the **first** request in a cycle, also attaches `hs(type="init")`. If a session key exists, wraps `"message"` in `sec`.

* **Responder**
  * `resp_confirm`: emits `"confirm"` and always attaches `hs(type="response")`.
  * `resp_exchange`: if a session key exists, wraps `"message"` in `sec`.

Other states (`conclude/finish/close`) are unchanged.

</details>

## SDK Features Used

| Feature                                                                             | Description                                                                                                                                                                |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                                                          | Instantiates the agent and sets up its logging context.                                                                                                                    |
| `client.flow()`                                                                     | Retrieves the flow engine that drives route-based orchestration.                                                                                                           |
| `client.flow().activate()`                                                          | Activates the flow engine so that route strings can be parsed and used to trigger handlers.                                                                                |
| `client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")` | Declares how arrows are drawn and parsed (e.g. parsing `stateA --> stateB`).                                                                                               |
| `Trigger = client_flow.triggers()` | Retrieves the flow engine's trigger objects (`ok`, `error`, `ignore`) used with `Move(Trigger.ok)`, `Stay(Trigger.ignore)`, etc. |
| `@client.upload_states()`                                                           | Registers the handler that reports the agent's current states to the client, driving the **receive** flow transitions.                                                     |
| `@client.download_states()`                                                         | Registers the handler that ingests the client's allowed states, updating in-memory state before the next **receive** cycle.                                                |
| `@client.hook(Direction.RECEIVE)`                                                   | Validates or filters all incoming payloads before they reach the route handlers.                                                                                           |
| `@client.hook(Direction.SEND)`                                                      | Augments or inspects all outbound payloads (e.g. tagging `from=my_id`).                                                                                                    |
| `@client.receive(route="A --> B")`                                                  | Registers an async handler for a specific route; the flow engine parses `"A --> B"` using the active arrow style.                                                          |
| `@client.send(route="sending", multi=True)`                                         | Background send-driver that wakes every tick (1 s) to emit maintenance duties (`register`, `finish`, `close`, `reconnect`).          |
| `@client.send(route="/all --> /all", multi=True, on_triggers={...})`                | Queued, event-driven send-driver that runs after receive events to avoid nonce races and double-emits.     |
| `client.logger`                                                                     | Centralized logger for all lifecycle events, ensuring consistent formatting and easy filtering.                                                                            |
| `client.loop.run_until_complete(setup())`                                           | Runs the `setup()` coroutine to create tables and indexes before the main loop starts.                                                                                     |
| `client.run(...)`                                                                   | Connects to the Summoner server and starts the asyncio event loop, coordinating both the **receive** and **send** workflows.                                               |

## `db_sdk` Features Used

| Feature                                         | Description                                                            |
| ----------------------------------------------- | ---------------------------------------------------------------------- |
| `Database(db_path)`                             | Provides a single async SQLite connection for all ORM operations.      |
| `Model.create_table(db)` / `Model.create_index` | Ensures required tables and indexes exist at startup.                  |
| `Model.get_or_create(db, ...)`                  | Finds or initializes a `RoleState` row for `(self_id, role, peer_id)`. |
| `Model.insert / find / update / delete`         | CRUD operations for managing per-peer state and logging nonce events.  |

## How to Run

Start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then run the agent, using a user-friendly name tag to keep identities and databases separated:

```bash
python agents/agent_HSAgent_1/agent.py --name alice
```

> [!CAUTION]
> Use a **unique** `--name` per process so each instance gets its **own** encrypted identity and database. Reusing a name makes agents share keys/DB, which can cause confusing state jumps, handshake hiccups, and hard-to-trace logs.
> **Fix:** run each process with a distinct `--name`. If a name was reused, stop both agents and either delete the matching `id_agent_<name>.json` and `HSAgent-<UUID>.db`, or just restart with a new `--name`.

Optional client config:

```bash
python agents/agent_HSAgent_1/agent.py --name alice --config configs/client_config.json
```

This is what you should see and expect:

* First, running with `--name alice` creates an **encrypted identity** at `id_agent_alice.json`.
* Then, a per-agent DB file `HSAgent-<UUID_for_alice>.db` is created next to the script.
* Finally, on shutdown (`Ctrl+C`), the database is closed cleanly.

## Simulation Scenarios

### Scenario 1: Two agents (same as `HSAgent_0`, now with crypto)

```bash
# Terminal 1: server
python server.py

# Terminal 2: agent (name 1)
python agents/agent_HSAgent_1/agent.py --name 1

# Terminal 3: agent (name 2)
python agents/agent_HSAgent_1/agent.py --name 2
```

You will see HELLO, nonce exchanges (`request`/`respond`), a request to conclude, and `finish`/`close` with reference checks. Early in the flow you should also see `sym_key=...` logs when `hs` is validated, and later **secure envelopes** being opened when `sec` is present.

**Walkthrough with storage dynamics (abridged):**

1. **HELLO / Register + Responder's `confirm` (with `hs:type="response"`)**

   * **Initiator** receives `confirm` with `my_nonce=<n1>` and `hs`.
   * *What happens:* initiator **derives** `SYM_KEYS[("initiator", peer)]` in **RAM**, sets `PEER_SIGN_PUB`, and **records** the peer's `my_nonce` once via `record_received_nonce_once(...)` in `NonceEvent(..., flow="received")`.
   * *Replay defense:* `hs.nonce` is checked against the just-seen `my_nonce` and via `DBNonceStore` (TTL **60s**).

   Example logs to look for:

   ```
   [init_ready -> init_exchange] sym_key=<...>...
   [init_ready -> init_exchange] peer_nonce set: <n1>
   ```

2. **Initiator's first `request` (with `hs:type="init"`)**

   * **Responder** receives `request` echoing `your_nonce=<n1>` plus `my_nonce=<n2>` and `hs`.
   * *What happens:* responder **derives** `SYM_KEYS[("responder", peer)]` in **RAM**, sets `PEER_SIGN_PUB`, and **records** the peer's `my_nonce` once via `record_received_nonce_once(...)` in `NonceEvent(..., flow="received")`.
   * If a message is present, it may already be sent as **`sec`**, which the responder will **open** using the session key; `last_secure_at` is updated (best-effort persist).

   Example:

   ```
   [resp_confirm -> resp_exchange] sym_key=<...>...
   [secure:responder] opened message: 'How are you?'
   ```

3. **Ping-pong (request/respond)**

   * Each side **sends** a fresh `my_nonce` → `NonceEvent(..., flow="sent")`.
   * Each **receives** the peer's `my_nonce` → stored **once** via `record_received_nonce_once(...)`.
   * If a duplicate `my_nonce` arrives (replay/dup), the handler logs and **ignores** it.
   * With a session key present, plain `"message"` is **sealed** to `sec` on send and **opened** on receive.

   Example:

   ```
   [send][initiator:init_exchange] request #k | my_nonce=<nk>
   [init_exchange -> init_finalize_propose] GOT RESPONSE #k
   [secure:initiator] opened message: 'I am OK!'
   ```

4. **Conclude / Finish / Close (finalize rule)**

   * **Initiator** sends `conclude(my_ref=r1)`.
   * **Responder** replies `finish(your_ref=r1, my_ref=r2)` → initiator stores `peer_reference=r2` and **clears NonceEvent** for its `(role="initiator", peer)` (transient exchange is done).
   * **Initiator** sends `close(your_ref=r2, my_ref=r1)` until the responder acknowledges.
   * **Responder**, on valid `close`, **clears NonceEvent** for `(role="responder", peer)` and returns to `resp_ready`.

   Example:

   ```
   [init_finalize_propose -> init_finalize_close] CLOSE
   [resp_finalize -> resp_ready] CLOSE SUCCESS
   # (nonce rows for the pair are now deleted)
   ```

5. **Reconnect (same run, same identities)**

   * If both sides keep their `peer_reference`/`local_reference`, the initiator may attempt a reconnect by presenting the responder's prior `my_ref`.
   * New exchange/handshake will **re-derive** a fresh session key; previous RAM key dies with the process.

> [!TIP]
> * Look for `sym_key=...` once per side, per handshake cycle.
> * `secure:* opened message` confirms the session key is being used.
> * After `CLOSE SUCCESS`, any subsequent exchange will re-populate `NonceEvent` from scratch.

### Scenario 2: Multi-peer

```bash
# Terminal 1: server
python server.py

# Single instance
python agents/agent_HSAgent_1/agent.py --name 1

# Additional instances (multi-peer handshake demo)
python agents/agent_HSAgent_1/agent.py --name 2
python agents/agent_HSAgent_1/agent.py --name 3
python agents/agent_HSAgent_1/agent.py --name 4
```

With three or more agents, a single instance interleaves **per-peer** actions keyed by `(self_id, role, peer_id)`. You'll observe **separate** session-key derivations (`sym_key=...`) and **separate** nonce logs per peer. Each peer pair's `NonceEvent` is cleared independently on a successful `close`.
