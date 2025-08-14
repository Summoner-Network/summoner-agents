# `HSAgent_1`

A multi-peer **handshake** agent that keeps the [`HSAgent_0`](../agent_HSAgent_0/) flow and state model but adds end-to-end cryptography and key protection. It **signs the handshake** (`hs`, Ed25519), performs an **ephemeral X25519** exchange + HKDF to derive a **symmetric session key**, can wrap payloads in **secure envelopes** (`sec`, AES-GCM with an Ed25519 envelope signature), and persists per-agent keys in an **encrypted identity file** (`id_agent_<name>.json`). All **client-side** Summoner SDK routes and the ORM-backed DB state remain the same as `HSAgent_0` (with optional crypto metadata columns on [`RoleState`](./db_models.py)).

> [!NOTE]
> This is an orchestration/state demo with a strong crypto veneer; key management is simplified. The identity file is **encrypted at rest**: private keys are sealed with **AES-GCM** using a key derived from a passphrase via **scrypt** ($N=2^{14}$, $r=8$, $p=1$). The JSON on disk contains only version/KDF metadata, salt, nonce, and ciphertext — **never** raw private key bytes. For real deployments, supply a strong passphrase (env var, OS keychain, or KMS), not the demo default.

<!-- > [!CAUTION]
> Use a unique `--name` per process so each instance gets its **own** encrypted identity and database; reusing a name makes processes share keys/DB. -->

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

> 📝 **Note:**
> **Peer scoping:** Upload now returns **per-peer** keys in the form `"initiator:<peer_id>"` and `"responder:<peer_id>"`. The download handler splits that key to target the exact `(self_id, role, peer_id)` row instead of updating all rows for a role.
>
> **Guard:** The receive hook drops payloads that lack a valid `from` (i.e., `content["from"] is None`) to avoid creating or mutating a thread with `peer_id=None`.

### What's added

1. **Signed handshake (`hs`)**

   * On the **first** `request` (initiator) and on `confirm` (responder), agents attach `hs` with:

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
   * The receiver validates `hs`, checks replay/TTL using the **DB-backed nonce store**, and derives a **32-byte session key** via X25519+HKDF.

2. **Secure envelope (`sec`)** *(optional)*

   * After a session key is derived, plain `"message"` fields may be replaced with:

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
   * The receiver verifies the envelope signature, decrypts (AES-GCM), checks the hash, and then surfaces the plaintext as `content["message"]`.

3. **Identity persistence**

   * Each agent loads/saves `id_agent_<name>.json` (AES-GCM sealed with a password-derived key via scrypt).
   * File contains `my_id` + private/public keys (X25519/Ed25519).

> 📝 **Note:** Nonces/references for the flow are still stored in `RoleState`/`NonceEvent` exactly as in `HSAgent_0`. The **handshake nonces** inside `hs` are checked via the same `NonceEvent` table through a small adapter.

### Receive routes (selected deltas)

* **Responder**

  * `resp_confirm → resp_exchange`
    Validates `"request"` as before. If `content["hs"]` is present and valid, derives `SYM_KEYS[("responder", peer_id)]` and records `PEER_SIGN_PUB`.
  * `resp_exchange → resp_finalize`
    Same ping-pong/conclude behavior. If `sec` is present and keys are known, decrypts and logs the clear `message`.

* **Initiator**

  * `init_ready → init_exchange`
    On `"confirm"`, if `hs` present/valid, derives `SYM_KEYS[("initiator", peer_id)]`.
  * `init_exchange → init_finalize_propose`
    Same checks; if `sec` is present, decrypts `message`.

### Send driver (per role & peer)

* **Initiator**

  * `init_exchange`: emits `"request"`. On the **first** request in a cycle, also attaches `hs` with `"type": "init"`. If a session key exists, wraps `"message"` in `sec`.

* **Responder**

  * `resp_confirm`: emits `"confirm"` and always attaches `hs` with `"type": "response"`.
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
| `client_flow.ready()`                                                               | Compiles regex patterns for the declared arrow style, enabling runtime parsing of route definitions.                                                                       |
| `Trigger = client_flow.triggers()`                                                  | Loads trigger names from the `TRIGGERS` file (containing `ok`, `error`, `ignore`), which are used in `Move(Trigger.ok)`, `Stay(Trigger.ignore)` and `Stay(Trigger.error)`. |
| `@client.upload_states()`                                                           | Registers the handler that reports the agent's current states to the server, driving the **receive** flow transitions.                                                     |
| `@client.download_states()`                                                         | Registers the handler that ingests the server's allowed states, updating in-memory rows before the next **receive** cycle.                                                 |
| `@client.hook(Direction.RECEIVE/SEND)`                                              | Same as `HSAgent_0`; adds no crypto here (crypto happens inside routes/payloads).                                                                                          |
| `@client.receive(route="A --> B")`                                                  | Same route map as `HSAgent_0`; crypto validation/decryption are added inside specific handlers.                                                                            |
| `@client.send(route="sending", multi=True)`                                         | Same send driver; now attaches `hs` and/or `sec` when appropriate.                                                                                                         |
| `client.logger`                                                                     | Centralized logging.                                                                                                                                                       |
| `client.run(...)`                                                                   | Starts the agent loop.                                                                                                                                                     |

## `db_sdk` Features Used

| Feature                                        | Description                                                                  |
| ---------------------------------------------- | ---------------------------------------------------------------------------- |
| `Database(db_path)`                            | Single async SQLite connection.                                              |
| `RoleState.create_table / create_index`        | Same as `HSAgent_0`; adds optional crypto metadata columns (safe if unused). |
| `NonceEvent.create_table / create_index`       | Used for nonce logging **and** handshake replay/TTL via `DBNonceStore`.      |
| `Model.get_or_create / insert / find / update` | Manage per-peer state and append nonce events.                               |

## How to Run

Start the Summoner server:

```bash
python server.py
```

> [!NOTE]
> You can use `--config configs/server_config_nojsonlogs.json` for cleaner terminal output.

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

You will see the HELLO, nonce exchanges (`request`/`respond`), a request to conclude, and `finish`/`close` with reference checks. Early in the flow you should also see `sym_key=...` logs when `hs` is validated, and later **secure envelopes** being opened when `sec` is present.

**Detailed terminal behavior (abridged):**

* **HELLO / Register (+ signed handshake)**

  ```
  ... - INFO - [send tick]
  ... - INFO - [resp_ready -> resp_confirm] REGISTER | peer_id=<peer>
  ... - INFO - [send][responder:resp_confirm] confirm | my_nonce=<n1>
  ... - INFO - [init_ready -> init_exchange] sym_key=<...>...   # after validating 'hs'
  ```
* **First inbound request → exchange begins**

  ```
  ... - INFO - [resp_confirm -> resp_exchange] check local_nonce='<n1>' ?= your_nonce='<n1>'
  ... - INFO - [resp_confirm -> resp_exchange] FIRST REQUEST
  ... - INFO - [init_ready -> init_exchange] peer_nonce set: <n2>
  ```
* **Ping-pong (a few rounds) with optional secure envelopes**

  ```
  ... - INFO - [send][initiator:init_exchange] request #1 | my_nonce=<n3>
  ... - INFO - [secure:responder] opened message: 'How are you?'
  ... - INFO - [init_exchange -> init_finalize_propose] RESPOND
  ... - INFO - [resp_exchange -> resp_finalize] REQUEST RECEIVED #2
  ```
* **Conclude / Finish / Close**

  ```
  ... - INFO - [send][initiator:init_finalize_propose] conclude #1 | my_ref=<r1>
  ... - INFO - [send][responder:resp_finalize] finish #1 | my_ref=<r2>
  ... - INFO - [resp_finalize -> resp_ready] CLOSE SUCCESS
  ... - INFO - [init_finalize_propose -> init_finalize_close] CLOSE
  ```
* **Reconnect attempts (same run)**

  ```
  ... - INFO - [send][initiator:init_ready] reconnect with <peer> under <peer_reference>
  ... - INFO - [resp_ready -> resp_confirm] RECONNECT | peer_id=<...> under my_ref=<...>
  ```

> [!NOTE]
> As in `HSAgent_0`, `my_id` is generated on first identity creation. Across **fresh identities**, peers will not auto-reconnect (a new HELLO occurs).

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

With three or more agents, one instance will interleave **per-peer** actions keyed by `(self_id, role, peer_id)`, just like `HSAgent_0`. You will also observe multiple `sym_key=...` derivations — one per peer pairing.
