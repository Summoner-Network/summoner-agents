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

    * **Unique index** on `(self_id, role, peer_id)` (conversation thread).
    * **Scan index** on `(self_id, role)` (send loops).
    * **`NonceEvent`** — append-only nonce log for the current conversation; cleared when finalize succeeds.

    * **Index** on `(self_id, role, peer_id)` for fast filtering.
    * **Replay guard:** we only de-dup **received** nonces (`flow='received'`). `flow='sent'` is audit-only.

2. During state sync, upload/download keeps flow and DB aligned:

    * `@client.upload_states()` reports **peer-scoped** keys for the inbound peer, e.g.
    `{"initiator:<peer>": <state>, "responder:<peer>": <state>}`.
    If no `from` is present, it returns `{}` (don't advertise globals).

    * `@client.download_states()` ingests allowed nodes and writes the chosen node to **that** `(self_id, role, peer_id)` row.

    * Unknown peers default to `init_ready` / `resp_ready`.

    > 📝 **Note:**
    > **Peer scoping & guards:**
    > * Upload returns keys of the form `"initiator:<peer_id>"`, `"responder:<peer_id>"`.
    > * Download splits the key to target exactly one row.
    > * Download ignores global per-role keys that lack a ":" (we only accept peer-scoped entries)
    > * The **receive hook** drops payloads without `from`, or with `to != my_id` when `to` is not `None`.

    Typical cues:

    ```
    [upload] peer=<...> | initiator=<state> | responder=<state>
    [download] possible states 'responder:<peer>': [Node(resp_confirm)]
    [download] 'responder' set state -> 'resp_confirm' for <peer>
    ```

3. On receive, each route validates, updates DB, and either `Move(Trigger.ok)` or `Stay(Trigger.ignore)`:

    **Responder side**

    * `resp_ready → resp_confirm` — HELLO / reconnect intake
    Accept `"register"` (fresh hello) when `to` is `None` (broadcast) and we don't hold a `local_reference`.
    Accept `"reconnect"` when `your_ref == local_reference`; clear our `local_reference` to allow a clean finalize.

    ```
    [resp_ready -> resp_confirm] REGISTER | peer_id=<peer>
    # or:
    [resp_ready -> resp_confirm] RECONNECT | peer_id=<...> under my_ref=<...>
    ```

    * `resp_confirm → resp_exchange` — first request validation
    Require `intent="request"`, `your_nonce == local_nonce` (echo), and a fresh `my_nonce`.
    Reject if that `my_nonce` was already **received** (replay guard).
    Accepting clears `local_nonce` (sender will mint), sets `exchange_count=1`, logs the received nonce.

    ```
    [resp_confirm -> resp_exchange] check local_nonce='<n1>' ?= your_nonce='<n1>'
    [resp_confirm -> resp_exchange] FIRST REQUEST
    ```

    * `resp_exchange → resp_finalize` — ping-pong or accept conclude
    With `intent="request"`: echo + replay checks, bump `exchange_count`, store peer's `my_nonce`, clear ours.
    With `intent="conclude"`: capture initiator's `my_ref`, reset `exchange_count`, move to finalize.

    ```
    [resp_exchange -> resp_finalize] REQUEST RECEIVED #2
    # or:
    [resp_exchange -> resp_finalize] REQUEST TO CONCLUDE
    ```

    * `resp_finalize → resp_ready` — close & cleanup
    Expect initiator's `intent="close"` with **both** refs; require `your_ref == local_reference`.
    On success: persist `peer_reference`, clear nonces/counters, **delete** `NonceEvent` log.
    While waiting, we bump `finalize_retry_count`; if it **exceeds** `RESP_FINAL_LIMIT`, wipe refs and return to ready.

    ```
    [resp_finalize -> resp_ready] CLOSE SUCCESS
    # or, timeout path:
    [resp_finalize -> resp_ready] FINALIZE RETRY LIMIT REACHED | FAILED TO CLOSE
    ```

    **Initiator side**

    * `init_ready → init_exchange` — HELLO intake
    Receive responder's `"confirm"` with `my_nonce`; store as `peer_nonce`, clear `local_nonce` and both refs.

    ```
    [init_ready -> init_exchange] peer_nonce set: <n2>
    ```

    * `init_exchange → init_finalize_propose` — respond or cut to finalize
    Expect `"respond"` with `your_nonce == local_nonce` (echo) and a fresh `my_nonce` not previously **received**.
    If `exchange_count > EXCHANGE_LIMIT`, cut to finalize; otherwise stay in exchange.

    ```
    [init_exchange -> init_finalize_propose] GOT RESPONSE #1
    # or:
    [init_exchange -> init_finalize_propose] EXCHANGE CUT (limit reached)
    ```

    * `init_finalize_propose → init_finalize_close` — finish accepted
    Expect responder's `"finish"` with `your_ref == local_reference` plus peer's `my_ref`.
    Persist `peer_reference`, clear `NonceEvent` log, and proceed to the close loop.

    ```
    [init_finalize_propose -> init_finalize_close] CLOSE
    ```

    * `init_finalize_close → init_ready` — back to idle (reconnect enabled)
    If `finalize_retry_count > INIT_FINAL_LIMIT`, cut back to ready **but keep both refs** so we can reconnect during the same run.

    ```
    [init_finalize_close -> init_ready] CUT (refs preserved)
    ```

    > 📝 **Note:**
    > Handlers are **guarded/idempotent** — if checks fail, the handler returns `Stay(Trigger.ignore)` and you will see a short log, not a state change.

4. On send, two drivers avoid races and keep logs readable:

    * `@client.send(route="sending", multi=True)` — background sender (\~1s)
    Drives periodic duties independent of a specific receive:

    * Initiator: `reconnect` when `peer_reference` is known; `close` retries while in `init_finalize_close`.
    * Responder: `finish` while in `resp_finalize`.
    * Always emits broadcast `{"intent":"register","to":null}`.

    ```
    [send tick]
    [send][initiator:init_ready] reconnect with <peer> under <ref>
    [send][responder:resp_finalize] finish #<k> | my_ref=<...>
    [send][initiator:init_finalize_close] close #<k> | your_ref=<...>
    ```

    * `@client.send(route="/all --> /all", multi=True, on_triggers={Trigger.ok, Trigger.error})` — queued sender (hub)
    Runs **after** receive handlers complete, so it reads the freshest DB state (e.g., `local_nonce` recently cleared).
    Drives chatty paths:

    * Initiator: `request` loop and `conclude`.
    * Responder: `confirm` and `respond`.

    ```
    [queued send tick]
    [send][initiator:init_exchange] request #<i> | my_nonce=<...>
    [send][responder:resp_exchange] respond #<i> | my_nonce=<...>
    [send][initiator:init_finalize_propose] conclude #<j> | my_ref=<...>
    ```

    > 📝 **Note:**
    > Because the queued sender fires *after* receives, you should see less overlap than with the background sender: clusters of receive logs followed by a single “queued send tick” that emits the appropriate messages.

    > 📝 **Note:**
    > The drivers are declared with `multi=True`, so one tick can emit **multiple payloads** (e.g., an initiator message, a responder message, and the broadcast register).

5. On storage & identity, each run is isolated:

    * A per-agent SQLite file `HSAgent-{my_id}.db` is created next to the script and closed on shutdown.
    * `my_id` is generated at start, so reconnect works **within the same run**; across restarts, a fresh HELLO occurs.

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

Then run the agent:

```bash
python agents/agent_HSAgent_0/agent.py
```

If you run **one agent** (server + a single client) you will only see periodic broadcasts and ticks; no handshake can complete without a peer:
```bash
[send tick]
[queued send tick]
[send][hook] {'to': None, 'intent': 'register', ...}
```
Start a second agent in another terminal to observe HELLO → exchange → finalize → close.

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
    ... - INFO - [init_exchange -> init_finalize_propose] GOT RESPONSE #1
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