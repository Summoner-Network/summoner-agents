# `HSBuyAgent_0`

A single-role **responder** that keeps the [`HSAgent_0`](../agent_HSAgent_0/) handshake and nonce model and layers a **buying workflow** on top. The negotiation overlay is composed via lightweight wrappers so that most route handlers look like plain `HSAgent_0` with a few decorator-style injections.

> [!NOTE]
> **Composition pattern (handshake-first):** `decoration_generator(route)` wraps the seller→buyer handlers to enforce handshake invariants (addressing, intent, nonce echo, duplicate-nonce drop) **before** running any negotiation code. This keeps trading logic orthogonal to the security and handshake layer.

This agent relies on shared models in [`db_models.py`](./db_models.py):

* **Handshake:** `RoleState`, `NonceEvent`
* **Negotiation:** `TradeState`, `History` (helpers: `start_negotiation_buyer`, `add_history`, `show_statistics`)

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

The responder state machine mirrors `HSAgent_0` with a split exchange phase:

* **Responder:** `resp_ready → resp_confirm → resp_exchange_0 → resp_exchange_1 → resp_finalize → resp_ready`
* The exchange length is driven by the **initiator's** `EXCHANGE_LIMIT`; the buyer keeps responding until the initiator cuts to `conclude`.

> 📝 **Note:**
> **Storage and invariants**
>
> * **RoleState** is scoped per `(self_id, role="responder", peer_id)` and tracks: `state`, `local_nonce`, `peer_nonce`, `local_reference`, `peer_reference`, `exchange_count`, `finalize_retry_count`, and `peer_address`.
> * **NonceEvent** logs all `my_nonce` and `your_nonce` traffic for the active conversation (`flow ∈ {"sent","received"}`) and is **cleared** for `(self_id, role, peer_id)` after a successful finalize and close.
> * **Echo rule:** every `request` and `respond` must satisfy `your_nonce == last counterpart local_nonce`.
> * **Finalize rule:** `conclude(my_ref) → finish(your_ref,my_ref) → close(your_ref,my_ref)` must match.

### What is added (buying workflow)

The agent acts as a **buyer** while staying on the **responder** track for the handshake. It exchanges compact JSON messages under `content["message"]`:

* **Outbound (buyer → seller):**

  ```json
  {"type":"buying","status":"offer"|"resp_interested"|"resp_accept"|"resp_accept_too"|"resp_refuse"|"resp_refuse_too","price":<float>,"TXID":<uuid?>}
  ```
* **Inbound (seller → buyer):**

  ```json
  {"type":"selling","status":"offer"|"init_interested"|"init_accept"|"init_refuse"|"init_accept_too"|"init_refuse_too","price":<float>,"TXID":<uuid?>}
  ```

*`start_negotiation_buyer` seeds `TradeState` using the seller's `transaction_id` and initializes `limit_acceptable_price`, `price_shift`, and `current_offer`.*

**Negotiation overlay (fork and merge routes):**

* `resp_exchange_0 → resp_interested`
  Seller sends `type=selling, status=offer`. Buyer compares `price` to `limit_acceptable_price` and either moves to `resp_interested` (ready to deal) or stays in `resp_exchange_0` after **increasing** its `current_offer`.

* `resp_exchange_0 → resp_accept` or `resp_refuse`
  On `status=init_interested`, accept or refuse based on price vs limit.

* `resp_interested → resp_exchange_1, resp_accept_too` and `resp_interested → resp_exchange_1, resp_refuse_too`
  On `status ∈ {init_accept, init_refuse}`, log the outcome in `History` (via `add_history`) and branch into the corresponding side path (`*_too`) before merging to `resp_exchange_1`.

* `resp_accept → resp_exchange_1` and `resp_refuse → resp_exchange_1`
  Merge paths for `init_accept_too` and `init_refuse_too` that also log `History`.

### Receive routes (responder)

* `resp_ready → resp_confirm` (HELLO / reconnect gate)
  Accepts `register` (fresh hello) if `to` is `None` and we have **no** `local_reference`; accepts `reconnect` if `your_ref` equals our last `local_reference`. Ensures/updates the `RoleState` row and refreshes `peer_address`. On reconnect, clears `local_reference` so a new finalize can proceed cleanly.

* `resp_confirm → resp_exchange_0`
  Validates the **first** `request` and nonce echo; stores `peer_nonce`, logs `NonceEvent(flow="received")`, sets `exchange_count=1`, and **starts a trade** for that peer using the seller's `TXID` (`start_negotiation_buyer(...)` initializes `TradeState` with price bounds).

* `resp_exchange_1 → resp_finalize`
  Accepts either:

  * `request` (normal ping-pong): checks echo, logs nonce, bumps `exchange_count`, stores `peer_nonce`, and stays in exchange; or
  * `conclude(my_ref)` from the initiator: captures `peer_reference`, resets `exchange_count`, and moves to `resp_finalize`.

* `resp_finalize → resp_ready`
  Validates the initiator's `close(your_ref,my_ref)` against our `local_reference`. On success, persists `peer_reference`, clears the per-peer `NonceEvent` log, zeros counters, logs **CLOSE SUCCESS**, and **ends the trade** (statistics + state reset).

### Send drivers

* **Background tick** `@client.send(route="sending", multi=True)`
  In `resp_finalize`, sends `finish(your_ref=peer_reference, my_ref=<local_ref>)`. Waits until `peer_reference` is known. (Initiator will follow with `close`.)

* **Queued sender** `@client.send(route="/all --> /all", multi=True, on_triggers={ok, error})`

  * In `resp_confirm`, generates a fresh `my_nonce`, logs `NonceEvent(flow="sent")`, and sends `confirm(my_nonce)`.
  * In `resp_exchange_0/1`, generates a fresh `my_nonce`, logs it, and sends `respond(your_nonce=peer_nonce, my_nonce=...)` with a **buying** message derived from `TradeState` (`current_offer`, `agreement`, `transaction_id`).

### State advertising (upload and download)

* **Upload** returns peer-scoped keys only, e.g. `{ "responder:<peer>": <state> }` or `{ "buyer:<peer>": <agreement> }`.
  When the responder is at `resp_exchange_0`, upload advertises the **buyer** decision key so the counterpart can merge or fork correctly.

* **Download** selects one allowed state by preference:

  * Responder preference: `resp_ready > resp_finalize > resp_confirm > resp_exchange_1 > resp_exchange_0`
  * Buyer decisions: `resp_refuse_too > resp_refuse > resp_accept_too > resp_accept > resp_interested > resp_exchange_0`
    If `resp_exchange_1` is allowed on the buyer side, it proactively advances the responder row to `resp_exchange_1` to merge paths.

### Tunables

* `RESP_FINAL_LIMIT = 5` (responder stays in `resp_finalize` and retries `finish` until a valid `close` arrives or this limit is exceeded)

</details>

## SDK Features Used

| Feature                                                                             | Description                                                                                                          |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                                                          | Instantiates the agent and sets up its logging context (`HSBuyAgent_0`).                                             |
| `client.flow()`                                                                     | Retrieves the flow engine that drives route-based orchestration.                                                     |
| `client.flow().activate()`                                                          | Activates the flow engine so that route strings can be parsed and used to trigger handlers.                          |
| `client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")` | Declares how arrows are drawn and parsed, for example parsing `stateA --> stateB`.                                   |
| `Trigger = client_flow.triggers()`                                                  | Loads trigger names used in `Move(Trigger.ok)`, `Stay(Trigger.ignore)`, and `Stay(Trigger.error)`.                   |
| `@client.upload_states()`                                                           | Advertises peer-scoped allowed states (responder and buyer decision key).                                            |
| `@client.download_states()`                                                         | Ingests allowed states (per peer) and updates `RoleState.state` and `TradeState.agreement` using a preference order. |
| `@client.hook(Direction.RECEIVE)`                                                   | Validates addressing and intent and logs raw receive payloads.                                                       |
| `@client.hook(Direction.SEND)`                                                      | Tags outbound payloads with `from=my_id` and logs them.                                                              |
| `@client.receive(route="A --> B")`                                                  | Registers async route handlers for handshake and negotiation fork and merge transitions.                             |
| `@client.send(route="sending", multi=True)`                                         | Background tick that emits `finish` during finalize.                                                                 |
| `@client.send(route="/all --> /all", multi=True, on_triggers={...})`                | Queued sender that runs after receive events to avoid races and that handles `confirm` and `respond` emissions.      |
| `client.logger`                                                                     | Centralized logger with color hints for negotiation events.                                                          |
| `client.loop.run_until_complete(setup())`                                           | Creates tables and indexes before the main loop starts.                                                              |
| `client.run(...)`                                                                   | Connects to the Summoner server and starts the asyncio loop, coordinating both receive and send workflows.           |

## `db_sdk` Features Used

| Feature                                          | Description                                                                                                            |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `Database(db_path)`                              | Single async SQLite connection (`HSBuyAgent-<UUID>.db`) scoped to this process run.                                    |
| `Model.create_table / create_index`              | Ensures tables (`RoleState`, `NonceEvent`, `TradeState`, `History`) and indexes exist at startup.                      |
| `Model.insert / find / update / delete / exists` | CRUD utilities used across handshake and nonce logging and negotiation (`NonceEvent.exists`, `NonceEvent.delete`).     |
| Negotiation helpers                              | `start_negotiation_buyer`, `create_or_reset_state`, `get_state`, `set_state_fields`, `add_history`, `show_statistics`. |

**Indexes created** (see `setup()`):

* `RoleState`: `uq_role_peer(self_id, role, peer_id)` (UNIQUE), `ix_role_scan(self_id, role)`
* `NonceEvent`: `ix_nonce_triplet(self_id, role, peer_id)` and a composite unique index for `(self_id, role, peer_id, flow, nonce)` if present
* `TradeState`: `ix_state_agent(agent_id)`
* `History`: `ix_history_agent(agent_id)`, `idx_history_agent_tx(agent_id, txid)` (UNIQUE)

## How to Run

Start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Run the **seller** agent:

```bash
python agents/agent_HSSellAgent_0/agent.py
```

Run the buyer agent (this component) in another terminal:

```bash
python agents/agent_HSBuyAgent_0/agent.py
```

**What to expect**

* On `register`/`reconnect`, the buyer goes to `resp_confirm` and emits `confirm(my_nonce)`.
* After the seller's first `request`, the buyer enters `resp_exchange_0`, **starts a trade** (`TradeState` with random limits and the seller's `transaction_id`), and you'll see alternating `respond` messages with correct nonce echoes and colorized negotiation logs:

  * Offer / interested / accept / refuse transitions
  * `History` insertions on accept and refuse confirmations
* After `conclude/finish/close`, the buyer **clears `NonceEvent`** for that peer and prints session statistics with `show_statistics(...)`.

## Simulation Scenarios

### Scenario 1 — Basic purchase (happy path, symmetric to seller)

Run each in a separate terminal:

```bash
# Terminal 1 — server
python server.py

# Terminal 2 — seller
python agents/agent_HSSellAgent_0/agent.py

# Terminal 3 — buyer (this agent)
python agents/agent_HSBuyAgent_0/agent.py
```

> [!CAUTION]
> The buyer stays on the **responder** track. It drives `confirm → respond → finish` while the seller (initiator) drives `request → conclude → close`.

**Step 1 — Confirm (HELLO)**

```text
[send][responder:resp_confirm] confirm | my_nonce=...
... [send][hook] {'to': null, 'intent': 'register', ...}
```

**What is happening**

* Buyer advertises readiness by sending `confirm(my_nonce)`.
* It continues to broadcast `register` for discovery.

**State and nonce impact**

* `RoleState.state=resp_confirm`, `local_nonce` generated and logged as `flow="sent"`.

**Step 2 — First request (enter exchange) and seed trade**

```text
[resp_confirm -> resp_exchange_0] intent OK
[resp_confirm -> resp_exchange_0] validation OK
[resp_confirm -> resp_exchange_0] check local_nonce='...' ?= your_nonce='...'
[resp_confirm -> resp_exchange_0] FIRST REQUEST
[HSBuyAgent_0|xxxxx] Started with <peer> OFFER=<...>, MAX=<...>, TXID=<...>
```

**State and nonce impact**

* `peer_nonce` recorded (`flow="received"`), `exchange_count=1`.
* `TradeState` initialized via `start_negotiation_buyer(...)`.

**Step 3 — Respond #1 (counter offer)**

```text
[send][responder:resp_exchange_0] respond #1 | my_nonce=...
[HSBuyAgent_0|xxxxx] Offer to <peer> at $<price>
```

**Step 4 — Respond #2 (buyer signals interest)**

```text
[send][responder:resp_exchange_0] respond #2 | my_nonce=...
[HSBuyAgent_0|xxxxx] Interested by <peer> at $<price>
```

**Step 5 — Merge (`*_too`) and advance to `resp_exchange_1`**

```text
[resp_interested --> resp_exchange_1, resp_accept_too] check local_nonce='...' ?= your_nonce='...'
[download] 'buyer' changed 'responder' -> 'resp_exchange_1' for <peer>
[download] 'buyer' set state -> 'resp_accept_too' for <peer>
```

**Step 6 — Initiator keeps requesting; buyer transitions to finalize**

```text
[resp_exchange_1 -> resp_finalize] check local_nonce='...' ?= your_nonce='...'
[resp_exchange_1 -> resp_finalize] REQUEST RECEIVED #k
# or later in the flow:
[resp_exchange_1 -> resp_finalize] REQUEST TO CONCLUDE
```

**Step 7 — Finish and close (reference swap + cleanup)**

```text
[send][responder:resp_finalize] finish #n | my_ref=...
[resp_finalize -> resp_ready] CLOSE SUCCESS
[HSBuyAgent_0] Agent <peer_id> - Success rate: <...>% (.../...), Last TXID: <...>
```

**Step 8 — Late close after success**

```text
... [send][hook] {'to': null, 'intent': 'register', ...}
# An extra 'close' may arrive after success:
[recv][hook] {... 'intent': 'close', 'your_ref': '<local_ref>', 'my_ref': '<peer_ref>', ...}
```

**State and nonce impact**

* On valid **`close`**, the buyer stores `peer_reference` and **deletes all `NonceEvent` rows** for this `(role, peer)`. References persist and are used on reconnect within the same run.

### Scenario 2 — Multi-peer (concurrent buyer↔seller pairs)

```bash
# Terminal 1 — server
python server.py

# Multiple sellers
python agents/agent_HSSellAgent_0/agent.py
python agents/agent_HSSellAgent_0/agent.py

# Multiple buyers
python agents/agent_HSBuyAgent_0/agent.py
python agents/agent_HSBuyAgent_0/agent.py
```

**What happens**
Multiple sellers (initiators) broadcast `register`. Each buyer (responder) sends a `confirm` **per seller** and maintains **independent** conversations keyed by `(self_id, role="responder", peer_id)`. Logs interleave across peers: parallel `respond/request` ping-pong, merges (`*_too`), and separate `finish/close` sequences **per pair**. For every buyer↔seller pair, `TradeState` (`offer`/`limit`/`transaction_id`) and `NonceEvent` are isolated; when the initiator cuts to finalize, the buyer follows with `finish`, and on `CLOSE SUCCESS` the nonce rows are deleted. Upload/download remains **peer-scoped**, so one peer's decision keys never leak into another's flow.
