# `HSSellAgent_0`

A single-role **initiator** that keeps the [`HSAgent_0`](../agent_HSAgent_0/) handshake and nonce model and layers a **selling workflow** on top. The negotiation overlay is composed via lightweight wrappers so that most route handlers look like plain `HSAgent_0` with a few decorator-style injections.

> [!NOTE]
> **Composition pattern (handshake-first):** `decoration_generator(route)` wraps the buyer→seller handlers to enforce handshake invariants (addressing, intent, nonce echo, duplicate-nonce drop) **before** running any negotiation code. This keeps trading logic orthogonal to the security and handshake layer.

This agent relies on shared models in [`db_models.py`](./db_models.py):

* **Handshake:** `RoleState`, `NonceEvent`
* **Negotiation:** `TradeState`, `History` (helpers: `start_negotiation_seller`, `add_history`, `show_statistics`)

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

The initiator state machine mirrors `HSAgent_0` with a split exchange phase:

* **Initiator:** `init_ready → init_exchange_0 → init_exchange_1 → init_finalize_propose → init_finalize_close → init_ready`

> 📝 **Note:**
> **Storage and invariants**
>
> * **RoleState** is scoped per `(self_id, role="initiator", peer_id)` and tracks: `state`, `local_nonce`, `peer_nonce`, `local_reference`, `peer_reference`, `exchange_count`, `finalize_retry_count`, and `peer_address`.
> * **NonceEvent** logs all `my_nonce` and `your_nonce` traffic for the active conversation (`flow ∈ {"sent","received"}`) and is **cleared** for `(self_id, role, peer_id)` on successful **`finish`** (reference swap); the subsequent **`close`** loop is idempotent.
> * **Echo rule:** every `request` and `respond` must satisfy `your_nonce == last counterpart local_nonce`.
> * **Finalize rule:** `conclude(my_ref) → finish(your_ref,my_ref) → close(your_ref,my_ref)` must match.

### What is added (selling workflow)

The agent acts as a **seller** while staying on the **initiator** track for the handshake. It exchanges compact JSON messages under `content["message"]`:

* **Outbound (seller → buyer):**

  ```json
  {"type":"selling","status": "offer"|"init_interested"|"init_accept"|"init_accept_too"|"init_refuse"|"init_refuse_too", "price": <float>, "TXID": <uuid?>}
  ```
* **Inbound (buyer → seller):**

  ```json
  {"type":"buying","status": "offer"|"resp_interested"|"resp_accept"|"resp_refuse"|"resp_accept_too"|"resp_refuse_too", "price": <float>, "TXID": <uuid?>}
  ```

*`start_negotiation_seller` seeds `TradeState` with randomized `limit_acceptable_price`, `price_shift`, `current_offer`, and a fresh `transaction_id`.*

**Negotiation overlay (fork and merge routes):**

* `init_exchange_0 → init_interested`
  Buyer sends `type=buying, status=offer`. Seller compares `price` to `limit_acceptable_price` and either moves to `init_interested` (ready to deal) or stays in `init_exchange_0` after lowering `current_offer`.

* `init_exchange_0 → init_accept` or `init_refuse`
  On `status=resp_interested`, accept or refuse based on price vs limit.

* `init_interested → init_exchange_1, init_accept_too` and `init_interested → init_exchange_1, init_refuse_too`
  On `status ∈ {resp_accept, resp_refuse}`, log the outcome in `History` (via `add_history`) and branch into the corresponding side path (`*_too`) before merging to `init_exchange_1`.

* `init_accept → init_exchange_1` and `init_refuse → init_exchange_1`
  Merge paths for `resp_accept_too` and `resp_refuse_too` that also log `History`.

### Receive routes (initiator)

* `init_ready → init_exchange_0` (HELLO and confirm)
  Accepts `confirm` with `my_nonce`, stores `peer_nonce`, logs `NonceEvent(flow="received")`, and **starts a trade** for that peer via `start_negotiation_seller(...)` (initializes `TradeState` with price bounds and a fresh `transaction_id`).

* `init_exchange_1 → init_finalize_propose`
  Validates `respond` with correct nonce echo. If `exchange_count > EXCHANGE_LIMIT`, cut to finalize; otherwise store and advance nonces and remain in exchange.

* `init_finalize_propose → init_finalize_close`
  Validates `finish(your_ref, my_ref)`; on success store `peer_reference`, **clear `NonceEvent`** for the peer, and **end the trade** (`show_statistics(...)` logs stats and `TradeState.transaction_id` is reset).

* `init_finalize_close → init_ready`
  Guard that cuts back to ready if `finalize_retry_count > INIT_FINAL_LIMIT` (references preserved for reconnect).

### Send drivers

* **Background tick** `@client.send(route="sending", multi=True)`

  * Reconnects from `init_ready` if `peer_reference` is known.
  * Sends `close` repeatedly in `init_finalize_close` until `INIT_FINAL_LIMIT`.
  * Always broadcasts a `register` for discovery.

* **Queued sender** `@client.send(route="/all --> /all", multi=True, on_triggers={ok, error})`

  * In `init_exchange_0` and `init_exchange_1`, generates a fresh `my_nonce`, increments `exchange_count`, logs `NonceEvent(flow="sent")`, and sends a `request(your_nonce=my_peer_nonce, my_nonce=...)` that carries the **selling** message derived from `TradeState` (`current_offer`, `agreement`, `transaction_id`).
  * In `init_finalize_propose`, sends `conclude(my_ref)` and increments `finalize_retry_count`.

### State advertising (upload and download)

* **Upload** returns peer-scoped keys only, for example `{ "initiator:<peer>": <state> }` or `{ "seller:<peer>": <agreement> }`.
  When `initiator` is at `init_exchange_0`, the upload advertises the **seller** decision key so the counterpart can merge or fork correctly.

* **Download** selects one allowed state by preference:

  * Initiator preference: `init_ready > init_finalize_close > init_finalize_propose > init_exchange_1 > init_exchange_0`
  * Seller decisions: `init_refuse_too > init_refuse > init_accept_too > init_accept > init_interested > init_exchange_0`
    If `init_exchange_1` is allowed on the seller side, it proactively advances the initiator row to `init_exchange_1` to merge paths.

### Tunables

* `EXCHANGE_LIMIT = 3` (initiator cuts exchange to finalize after this many request and respond rounds)
* `INIT_FINAL_LIMIT = 3` (initiator repeats `close` at most this many times before returning to `init_ready`)

</details>

## SDK Features Used

| Feature                                                                             | Description                                                                                                          |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                                                          | Instantiates the agent and sets up its logging context (`HSSellAgent_0`).                                            |
| `client.flow()`                                                                     | Retrieves the flow engine that drives route-based orchestration.                                                     |
| `client.flow().activate()`                                                          | Activates the flow engine so that route strings can be parsed and used to trigger handlers.                          |
| `client_flow.add_arrow_style(stem="-", brackets=("[","]"), separator=",", tip=">")` | Declares how arrows are drawn and parsed, for example parsing `stateA --> stateB`.                                   |
| `client_flow.ready()`                                                               | Compiles regex patterns for the declared arrow style.                                                                |
| `Trigger = client_flow.triggers()`                                                  | Loads trigger names used in `Move(Trigger.ok)`, `Stay(Trigger.ignore)`, and `Stay(Trigger.error)`.                   |
| `@client.upload_states()`                                                           | Advertises peer-scoped allowed states (initiator and seller decision key).                                           |
| `@client.download_states()`                                                         | Ingests allowed states (per peer) and updates `RoleState.state` and `TradeState.agreement` using a preference order. |
| `@client.hook(Direction.RECEIVE)`                                                   | Validates addressing and intent and logs raw receive payloads.                                                       |
| `@client.hook(Direction.SEND)`                                                      | Tags outbound payloads with `from=my_id` and logs them.                                                              |
| `@client.receive(route="A --> B")`                                                  | Registers async route handlers for handshake and negotiation fork and merge transitions.                             |
| `@client.send(route="sending", multi=True)`                                         | Background tick that handles reconnect and close and always emits broadcast `register`.                              |
| `@client.send(route="/all --> /all", multi=True, on_triggers={...})`                | Queued sender that runs after receive events to avoid races and that handles `request` and `conclude` emissions.     |
| `client.logger`                                                                     | Centralized logger with color hints for negotiation events.                                                          |
| `client.loop.run_until_complete(setup())`                                           | Creates tables and indexes before the main loop starts.                                                              |
| `client.run(...)`                                                                   | Connects to the Summoner server and starts the asyncio loop, coordinating both receive and send workflows.           |

## `db_sdk` Features Used

| Feature                                          | Description                                                                                                             |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `Database(db_path)`                              | Single async SQLite connection (`HSSellAgent-<UUID>.db`) scoped to this process run.                                    |
| `Model.create_table / create_index`              | Ensures tables (`RoleState`, `NonceEvent`, `TradeState`, `History`) and indexes exist at startup.                       |
| `Model.insert / find / update / delete / exists` | CRUD utilities used across handshake and nonce logging and negotiation (`NonceEvent.exists`, `NonceEvent.delete`).      |
| Negotiation helpers                              | `start_negotiation_seller`, `create_or_reset_state`, `get_state`, `set_state_fields`, `add_history`, `show_statistics`. |

**Indexes created** (see `setup()`):

* `RoleState`: `uq_role_peer(self_id, role, peer_id)` (UNIQUE), `ix_role_scan(self_id, role)`
* `NonceEvent`: `ix_nonce_triplet(self_id, role, peer_id)` *(dedup handled in code via `NonceEvent.exists`; an optional UNIQUE `(self_id, role, peer_id, flow, nonce)` can be added but is **not** enabled in this sample)*
* `TradeState`: `ix_state_agent(agent_id)`
* `History`: `ix_history_agent(agent_id)`, `idx_history_agent_tx(agent_id, txid)` (UNIQUE)

## How to Run

Start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Run the seller agent (this component):

```bash
python agents/agent_HSSellAgent_0/agent.py
```

Run the **buyer** agent (in another terminal) so the handshake and negotiation can proceed (see Buyer README):

```bash
python agents/agent_HSBuyAgent_0/agent.py
```

**What to expect**

* On first contact (`confirm`), the seller seeds a per-peer `RoleState`, logs the inbound `my_nonce`, and **starts a trade** (`TradeState` with random limits and a new `transaction_id`).
* You will see alternating `request` and `respond` with correct nonce echoes and colorized negotiation logs:

  * Offer and interested and accept and refuse transitions
  * `History` insertions on accept and refuse confirmations
* On valid **`finish`**, the seller **clears `NonceEvent`** for that peer (reference swap); the subsequent **`close`** loop is idempotent and preserves references for reconnect during the same run.

> *Log formatting:* examples below show JSON-like snippets (use `null`); live logs from the Python runtime show `None`.

## Simulation Scenarios

### Scenario 1 - Basic sale (happy path)

Run each in a separate terminal:

```bash
# Terminal 1 - server
python server.py

# Terminal 2 - seller (this agent)
python agents/agent_HSSellAgent_0/agent.py

# Terminal 3 - buyer
python agents/agent_HSBuyAgent_0/agent.py
```

> [!CAUTION]
> The seller stays on the **initiator** track. It drives `request → conclude → close` while the buyer acknowledges with `confirm` and `respond` and `finish`.

**Step 1 - Reconnect and discovery (broadcast)**

```text
[send][hook] {'to': '80b6d53d-4ac2-4325-acce-ce5769420ff3', 'your_ref': '3723337987', 'intent': 'reconnect', 'from': '94666c7d-e190-4764-a0c3-dd300a14f9bd'}
... [send][hook] {'to': null, 'intent': 'register', 'from': '94666c7d-e190-4764-a0c3-dd300a14f9bd'}
```

**What is happening**

* The seller attempts a reconnect using the remembered `peer_reference`.
* It also broadcasts `register` each tick so peers can discover it.

**State and nonce impact**

* No nonce changes yet; this is only addressing and discovery.
* `RoleState.state` remains `init_ready` until a `confirm` arrives.

**Step 2 - Buyer confirms and seller enters exchange and seeds trade**

```text
[recv][hook] {... 'intent': 'confirm', 'my_nonce': '8792391879', 'from': '80b6d...'}
[init_ready -> init_exchange_0] intent OK
[init_ready -> init_exchange_0] validation OK
[init_ready -> init_exchange_0] peer_nonce set: 8792391879
[HSSellAgent_0|94666] Started with 80b6d53d-4... MIN=60.0, OFFER=69.0, TXID=6337...
```

**What is happening**

* On `confirm`, the seller stores the peer nonce and transitions to `init_exchange_0`.
* The seller initializes negotiation state (minimum acceptable price, current offer, transaction id).

**State and nonce impact**

* `RoleState.peer_nonce = 8792391879`, `exchange_count = 0`.
* `NonceEvent(flow="received")` for that nonce is recorded once.

**Step 3 - Request #1 (offer 69) and buyer counters (low offer)**

```text
[send][initiator:init_exchange_0] request #1 | my_nonce=7572976386
[HSSellAgent_0|94666] Offer to 80b6d53d-4 at $69.0
...
[recv][hook] {... 'intent': 'respond', 'your_nonce': '7572976386', 'my_nonce': '5866754819',
               'message': {'type': 'buying','status':'offer','price': 10.0, 'TXID':'6337...'}}

[init_exchange_0 --> init_interested] check local_nonce='7572976386' ?= your_nonce='7572976386'
... GOT RESPONSE #1
[HSSellAgent_0|94666] Decreased for 80b6d53d-4 at $65.0
```

**What is happening**

* Seller emits request number 1 with its `my_nonce`; buyer responds with a counter-offer.
* The decorator enforces the echo rule and the seller decreases its offer to 65.

**State and nonce impact**

* `RoleState.local_nonce` is generated and logged as `flow="sent"`.
* On response, buyer `my_nonce` is stored and logged once as `flow="received"`.
* Negotiation state updates `current_offer = 65`.

**Step 4 - Request #2 (offer 65) and buyer is interested**

```text
[send][initiator:init_exchange_0] request #2 | my_nonce=2129991974
[HSSellAgent_0|94666] Offer to 80b6d53d-4 at $65.0
...
[recv][hook] {... 'status': 'resp_interested', 'price': 65.0, 'TXID':'6337...'}
[init_exchange_0 --> init_accept] check local_nonce='2129991974' ?= your_nonce='2129991974'
... GOT RESPONSE #2
[HSSellAgent_0|94666] Will accept 80b6d53d-4 at $65.0
```

**Step 5 - Request #3 (seller accepts) and buyer accepts too, merge**

```text
[send][initiator:init_exchange_0] request #3 | my_nonce=8866759146
[HSSellAgent_0|94666] Accepts 80b6d53d-4 at $65.0
...
[recv][hook] {... 'status': 'resp_accept_too', 'price': 65.0, 'TXID':'6337...'}
[init_accept --> init_exchange_1] check local_nonce='8866759146' ?= your_nonce='8866759146'
... GOT RESPONSE #3
[download] 'seller' changed 'initiator' -> 'init_exchange_1' for 80b6d
```

**Step 6 - Request #4 and exchange is cut to finalize**

```text
[send][initiator:init_exchange_1] request #4 | my_nonce=5349289697
...
[recv][hook] {... 'your_nonce': '5349289697', 'my_nonce':'1573597647', 'status': 'resp_accept_too', ...}
[init_exchange_1 -> init_finalize_propose] check local_nonce='5349289697' ?= your_nonce='5349289697'
[init_exchange_1 -> init_finalize_propose] EXCHANGE CUT (limit reached)
```

**Step 7 - Conclude and finish (reference swap) and nonce cleanup**

```text
[send][initiator:init_finalize_propose] conclude #1 | my_ref=9615441415
...
[recv][hook] {... 'intent': 'finish', 'your_ref': '9615441415', 'my_ref': '7315562165', ...}
[init_finalize_propose -> init_finalize_close] check local_reference='9615441415' ?= your_ref='9615441415'
[init_finalize_propose -> init_finalize_close] CLOSE
[HSSellAgent_0] Peer 80b6d53d-4ac2-4325-acce-ce5769420ff3 - Success rate: 50.00% (1/2), Last TXID: 6337...
```

**Step 8 - Close loop (idempotent) and periodic register**

```text
[send][initiator:init_finalize_close] close #1 | your_ref=7315562165
...
[send][initiator:init_finalize_close] close #2 | your_ref=7315562165
```

**State and nonce impact**

* On valid **`finish`**, the seller stores `peer_reference` and **deletes all `NonceEvent` rows** for this `(role, peer)`. References persist and are used for reconnect during the same run.

### Scenario 2 - Multi-peer (concurrent seller↔buyer pairs)

```bash
# Terminal 1 — server
python server.py

# Multiple instances of SellAgent_0
python agents/agent_HSSellAgent_0/agent.py
python agents/agent_HSSellAgent_0/agent.py

# Multiple instances of BuyAgent_0
python agents/agent_HSBuyAgent_0/agent.py
python agents/agent_HSBuyAgent_0/agent.py
```

**What happens**

Multiple sellers (initiators) broadcast `register`, and each buyer (responder) sends a `confirm` **per seller**. Each seller then maintains **independent** conversations keyed by `(self_id, role="initiator", peer_id)`. Logs interleave across peers: you will see parallel `request/respond` ping-pong, occasional merges (`*_too`), and separate `conclude/finish/close` sequences **per pair**. For every seller↔buyer pair, `TradeState` (`offer`/`limit`/`transaction_id`) and `NonceEvent` are isolated; `exchange_count` reaches `EXCHANGE_LIMIT` and cuts to finalize **independently**. On successful **`finish`**, that pair's nonce rows are deleted, `History` is updated for the transaction, and the seller may attempt a `reconnect` while references remain valid **within the same run**. Upload/download remains **peer-scoped**, so one peer's decision keys never leak into another's flow.
