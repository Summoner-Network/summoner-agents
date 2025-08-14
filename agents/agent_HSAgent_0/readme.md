# `HSAgent_0`

A multi-peer **handshake** agent that uses [`db_sdk.py`](./db_sdk.py) to manage per-peer, per-role state (see SQL table **schemas** in [`db_models.py`](./db_models.py)); perform a nonce-exchange loop; finalize with short references; and demonstrate reconnect — all via **client-side** Summoner SDK routes and flow parsing.

> [!NOTE]
> **What this handshake demonstrates:**
> Two agents coordinate as **initiator** and **responder**. They **ping-pong nonces** (fresh short tokens) to confirm liveness and ordering, then **exchange short references** to mark the session as finalized. Those references enable **reconnect within the same run**. This demo is about orchestration and state; it is not a cryptographic handshake.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, `setup()` creates two tables and indexes:

   * **`RoleState`** — one row per `(self_id, role, peer_id)` with fields like `state`, `local_nonce`, `peer_nonce`, `local_reference`, `peer_reference`, `exchange_count`, `finalize_retry_count`, `peer_address`, timestamps.

     * **Unique index** on `(self_id, role, peer_id)` that identifies a conversation thread.
     * **Scan index** on `(self_id, role)` to speed the send loop.
   * **`NonceEvent`** — append-only nonce log for the current conversation; cleared when finalize succeeds.

     * **Index** on `(self_id, role, peer_id)` for fast filtering.

2. State sync (ROM → RAM)

   * `@client.upload_states()` surfaces DB-backed ("ROM") state for the peer (or defaults if unknown).
   * `@client.download_states()` receives allowed nodes and updates local rows so the **client** can deterministically choose which **receive** handler to run next.
   * Unknown peers default to `init_ready` / `resp_ready`.
   * Typical logs for this phase look like:

     ```
     [upload] peer=<...> | initiator=<state> | responder=<state>
     [download] possible states '<role>': [...]
     [download] '<role>' updating `state` to '<node>'
     ```

   > 📝 **Note:**
   > Routes are parsed and owned on the client (via `client.flow()`) while the server mainly relays.
   >
   > **Peer scoping:** Upload now returns **per-peer** keys in the form `"initiator:<peer_id>"` and `"responder:<peer_id>"`. The download handler splits that key to target the exact `(self_id, role, peer_id)` row instead of updating all rows for a role.
   >
   > **Guard:** The receive hook drops payloads that lack a valid `from` (i.e., `content["from"] is None`) to avoid creating or mutating a thread with `peer_id=None`.

3. Receive routes (what each transition means and what you will see):

   **Responder side**

   * `resp_ready → resp_confirm` — **HELLO / reconnect intake**
     Accept a fresh `"register"` or a `"reconnect"` carrying our remembered `local_reference` as their `your_ref`.
     *Terminal cues:*

     ```
     [resp_ready -> resp_confirm] REGISTER | peer_id=<peer>
     # or, for reconnect:
     [resp_ready -> resp_confirm] RECONNECT | peer_id=<...> under my_ref=<...>
     ```
   * `resp_confirm → resp_exchange` — **First request validation**
     Expect `intent="request"` with `your_nonce == local_nonce` from our previous confirm, and a new `my_nonce` from the peer.

     ```
     [resp_confirm -> resp_exchange] check local_nonce='<n1>' ?= your_nonce='<n1>'
     [resp_confirm -> resp_exchange] FIRST REQUEST
     ```
   * `resp_exchange → resp_finalize` — **Ping-pong or accept conclude**
     Either continue with `intent="request"` and increment `exchange_count`, or accept `intent="conclude"` carrying the initiator's `my_ref`.

     ```
     [resp_exchange -> resp_finalize] REQUEST RECEIVED #2
     # or:
     [resp_exchange -> resp_finalize] REQUEST TO CONCLUDE
     ```
   * `resp_finalize → resp_ready` — **Close**
     Expect `intent="close"` with both refs; verify `your_ref == local_reference`, then clear the nonce log.

     ```
     [resp_finalize -> resp_ready] CLOSE SUCCESS
     ```

   **Initiator side**

   * `init_ready → init_exchange` — **HELLO intake**
     Receive `intent="confirm"` with responder's `my_nonce`; store as `peer_nonce`.

     ```
     [init_ready -> init_exchange] peer_nonce set: <n2>
     ```
   * `init_exchange → init_finalize_propose` — **Respond or cut to finalize**
     Expect `intent="respond"` with `your_nonce == local_nonce`. If `exchange_count > EXCHANGE_LIMIT`, cut to finalize; otherwise keep ping-ponging.

     ```
     [init_exchange -> init_finalize_propose] RESPOND
     # or (limit reached):
     [init_exchange -> init_finalize_propose] EXCHANGE CUT (limit reached)
     ```
   * `init_finalize_propose → init_finalize_close` — **Finish**
     Expect `intent="finish"` with `your_ref == local_reference` and peer's `my_ref`.
     Clear the nonce log on success.

     ```
     [init_finalize_propose -> init_finalize_close] CLOSE
     ```
   * `init_finalize_close → init_ready` — **Back to idle**
     If `finalize_retry_count > FINAL_LIMIT`, cut back to ready while keeping references for potential reconnect in the **same run**.

     ```
     [init_finalize_close -> init_ready] CUT (refs preserved)
     ```

   > 📝 **Note:**
   > Handlers are **guarded/idempotent** — if checks fail, the handler returns `Stay(Trigger.ignore)` and you will see a short log, not a state change.

4. Send driver (every second, per peer & role)
   Emits role-appropriate messages based on each row's `state`:

   The driver is declared with `multi=True`, so one tick can emit **multiple payloads** (e.g., an initiator message, a responder message, and the broadcast register), which matches the interleaved logs.

   * **Initiator**:

     * `init_ready`: optional `"reconnect"` when we already know `peer_reference`.
     * `init_exchange`: `"request"` with a fresh `my_nonce` (and `your_nonce = peer_nonce`).
     * `init_finalize_propose`: `"conclude"` with `my_ref`.
     * `init_finalize_close`: `"close"` with both refs until acknowledged *(log line prefix is "finish", payload `intent` is `"close"`)*.

   * **Responder**:

     * `resp_confirm`: `"confirm"` with a fresh `my_nonce`.
     * `resp_exchange`: `"respond"` with fresh `my_nonce` (and `your_nonce = peer_nonce`).
     * `resp_finalize`: `"finish"` with `my_ref`.

   Every tick also broadcasts `{"intent": "register", "to": null}` for discovery.

5. Storage & identity
   A per-agent SQLite file `HSAgent-{my_id}.db` is created next to the script and closed on shutdown.
   `my_id` is generated at start, so reconnect works **within the same run**; after a restart a new HELLO occurs.
   `EXCHANGE_LIMIT` and `FINAL_LIMIT` are **operational counters** for demo flow control.

</details>

### SDK Features Used

| Feature                                                                             | Description                                                                                                                                                                |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                                                          | Instantiates the agent and sets up its logging context.                                                                                                                    |
| `client.flow()`                                                                     | Retrieves the flow engine that drives route-based orchestration.                                                                                                           |
| `client.flow().activate()`                                                          | Activates the flow engine so that route strings can be parsed and used to trigger handlers.                                                                                |
| `client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")` | Declares how arrows are drawn and parsed (e.g. parsing `stateA --> stateB`).                                                                                               |
| `client_flow.ready()`                                                               | Compiles regex patterns for the declared arrow style, enabling runtime parsing of route definitions.                                                                       |
| `Trigger = client_flow.triggers()`                                                  | Loads trigger names from the `TRIGGERS` file (containing `ok`, `error`, `ignore`), which are used in `Move(Trigger.ok)`, `Stay(Trigger.ignore)` and `Stay(Trigger.error)`. |
| `@client.upload_states()`                                                           | Registers the handler that reports the agent's current states to the server, driving the **receive** flow transitions.                                                     |
| `@client.download_states()`                                                         | Registers the handler that ingests the server's allowed states, updating in-memory state before the next **receive** cycle.                                                |
| `@client.hook(Direction.RECEIVE)`                                                   | Validates or filters all incoming payloads before they reach the route handlers.                                                                                           |
| `@client.hook(Direction.SEND)`                                                      | Augments or inspects all outbound payloads (e.g. tagging `from=my_id`).                                                                                                    |
| `@client.receive(route="A --> B")`                                                  | Registers an async handler for a specific route; the flow engine parses `"A --> B"` using the active arrow style.                                                          |
| `@client.send(route=..., multi=True)`                                               | Defines the periodic "send driver" that wakes every tick (1 s) to emit messages per peer based on `RoleState.state`.                                                       |
| `client.logger`                                                                     | Centralized logger for all lifecycle events, ensuring consistent formatting and easy filtering.                                                                            |
| `client.loop.run_until_complete(setup())`                                           | Runs the `setup()` coroutine to create tables and indexes before the main loop starts.                                                                                     |
| `client.run(...)`                                                                   | Connects to the Summoner server and starts the asyncio event loop, coordinating both the **receive** and **send** workflows.                                               |

### `db_sdk` Features Used

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

Then run the agent:

```bash
python agents/agent_HSAgent_0/agent.py
```

A per-agent database file (`HSAgent-{my_id}.db`) will be created next to the script.
On shutdown (`Ctrl+C`), the agent closes the database cleanly.

## Simulation Scenarios

### Scenario 1

```bash
# Terminal 1: server
python server.py

# First instance
python agents/agent_HSAgent_0/agent.py

# Second instance
python agents/agent_HSAgent_0/agent.py
```

You will see a HELLO, nonce exchanges (`request`/`respond`), a request to conclude, and then `finish`/`close` with reference checks, followed by `reconnect` attempts later in the same run, when references still match.

**Detailed terminal behavior (abridged):**

* **HELLO / Register**

  ```
  ... - INFO - [send tick]
  ... - INFO - [resp_ready -> resp_confirm] REGISTER | peer_id=<peer>
  ... - INFO - [send][responder:resp_confirm] confirm | my_nonce=<n1>
  ```
* **First inbound request → exchange begins**

  ```
  ... - INFO - [resp_confirm -> resp_exchange] check local_nonce='<n1>' ?= your_nonce='<n1>'
  ... - INFO - [resp_confirm -> resp_exchange] FIRST REQUEST
  ... - INFO - [init_ready -> init_exchange] peer_nonce set: <n2>
  ```
* **Ping-pong (a few rounds)**

  ```
  ... - INFO - [send][initiator:init_exchange] request #1 | my_nonce=<n3>
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
> `my_id` is generated in memory on each start. This means that across **restarts**, peers will not automatically reconnect (a new HELLO will occur).

### Scenario 2

```bash
# Terminal 1: server
python server.py

# Single instance
python agents/agent_HSAgent_0/agent.py

# Multiple instances (multi-peer handshake demo)
python agents/agent_HSAgent_0/agent.py
python agents/agent_HSAgent_0/agent.py
python agents/agent_HSAgent_0/agent.py
```

With three or more agents, one instance will interleave **per-peer** actions (keyed by `(self_id, role, peer_id)`): you will see alternating `confirm/respond/finish` (responder) and `request/conclude/close` (initiator) lines tagged with different peer IDs, demonstrating concurrent conversations.
