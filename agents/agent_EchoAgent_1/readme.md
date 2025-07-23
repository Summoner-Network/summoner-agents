# `EchoAgent_1`

A variant of [`EchoAgent_0`](../agent_EchoAgent_0/) that uses a receive hook to validate and filter incoming messages before buffering them. It demonstrates how to use `SummonerClient` with a buffered send/receive pipeline based on an internal message queue, enhanced with a validation hook.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.  
2. When a message arrives (`@client.receive`):
   - It first goes through a `@client.hook(direction=Direction.RECEIVE)` function:
     - If it is a string starting with `"Warning:"`, logs a warning with the `"Warning:"` prefix replaced by `[From Server]` and drops the message.  
     - If it is not a dict with `"addr"` and `"content"`, logs:
       ```
       [hook:recv] missing address/content
       ```
       and drops the message.  
     - If valid, logs:
       ```
       [hook:recv] <addr> passed validation
       ```
       and lets the message continue to the receive handler.
   - The `@client.receive` function then serializes the content and enqueues it into `message_buffer`, logging:
     ```
     Buffered message from:(SocketAddress=<addr>)
     ```
3. The `@client.send` coroutine:
   - Awaits `message_buffer.get()` to retrieve the next message.  
   - Sleeps for 1 second to simulate delay.  
   - Returns the original content, which is sent back to the server.  
4. The cycle continues indefinitely until the client is stopped (e.g., Ctrl+C).


> 📝 **Note:**
> The receive hook here is similar to the one used in [`RecvAgent_1`](../agent_RecvAgent_1/agent.py), showing how validation logic can be reused modularly across agents using Summoner's hook system.

</details>

## SDK Features Used

| Feature                                | Description                                           |
|----------------------------------------|-------------------------------------------------------|
| `SummonerClient(...)`                  | Instantiates and manages the agent                    |
| `@client.hook(direction=RECEIVE)`      | Filters and validates incoming messages               |
| `@client.receive(route=...)`           | Handles messages that passed the hook check           |
| `@client.send(route=...)`              | Emits buffered messages periodically                  |
| `client.logger`                        | Logs runtime events and debugging information         |
| `client.loop.run_until_complete(...)`  | Initializes the agent's internal message queue        |
| `client.run(...)`                  | Connects to the server and starts the asyncio event loop  |

## How to Run

First, ensure the Summoner server is running:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.


Then run the agent:

```bash
python agents/agent_EchoAgent_1/agent.py
```


## Simulation Scenarios

### Scenario 1: Echo Chamber (Echo ↔ Echo loop)

This setup uses two `EchoAgent_1` instances and one [`SendAgent_0`](../agent_SendAgent_0/). It demonstrates how messages from the sender propagate and then continue bouncing between the echo agents.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (EchoAgent instance #1)
python agents/agent_EchoAgent_1/agent.py

# Terminal 3 (EchoAgent instance #2)
python agents/agent_EchoAgent_1/agent.py

# Terminal 4 (SendAgent_0, briefly)
python agents/agent_SendAgent_0/agent.py
```

To seed the echo, connect `SendAgent_0` briefly, then kill it (`Ctrl+C`). You will see:

```bash
# In SendAgent_0
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 12:57:46.386 - SendAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
^C2025-07-23 12:58:35.443 - SendAgent_0 - INFO - Client is shutting down...
```

Then, in each `EchoAgent_1` terminal, you will observe:

* At first, messages are buffered **from the address of `SendAgent_0`**
* Shortly after disconnection, new messages appear **from the other EchoAgent**, confirming that the two echo agents are relaying messages between each other

Example:

```text
2025-07-23 12:57:44.037 - EchoAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 12:57:47.389 - EchoAgent_1 - INFO - [hook:recv] 127.0.0.1:53135 passed validation
2025-07-23 12:57:47.389 - EchoAgent_1 - INFO - Buffered message from:(SocketAddress=127.0.0.1:53135).
2025-07-23 12:57:48.391 - EchoAgent_1 - INFO - [hook:recv] 127.0.0.1:53135 passed validation
2025-07-23 12:57:48.392 - EchoAgent_1 - INFO - Buffered message from:(SocketAddress=127.0.0.1:53135).
2025-07-23 12:57:48.394 - EchoAgent_1 - INFO - [hook:recv] 127.0.0.1:53129 passed validation
2025-07-23 12:57:48.394 - EchoAgent_1 - INFO - Buffered message from:(SocketAddress=127.0.0.1:53129).  <-- from the other EchoAgent
```

This validates that the echo agents function not just as forwarders, but also as reflexive relays once seeded.


### Scenario 2: Echo as a Relay (Send → Echo → Recv)

This setup places `EchoAgent_1` between a sender and a receiver, allowing messages to arrive twice: once directly from the sender, and once via the echo agent after a delay.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (EchoAgent)
python agents/agent_EchoAgent_1/agent.py

# Terminal 3 (RecvAgent_0)
python agents/agent_RecvAgent_0/agent.py

# Terminal 4 (SendAgent_0)
python agents/agent_SendAgent_0/agent.py
```

#### What Happens

* [`SendAgent_0`](../agent_SendAgent_0/) sends messages directly to the server.
* `EchoAgent_1` receives these, buffers them, and re-sends them with a 1-second delay.
* [`RecvAgent_0`](../agent_RecvAgent_0/) receives **both**: first from `SendAgent_0`, then (a second later) from `EchoAgent_1`.

In `EchoAgent_1`, you will see:

```text
2025-07-23 13:01:31.766 - EchoAgent_1 - INFO - [hook:recv] 127.0.0.1:53195 passed validation
2025-07-23 13:01:31.767 - EchoAgent_1 - INFO - Buffered message from:(SocketAddress=127.0.0.1:53195).
```

In `RecvAgent_0`, both `SendAgent_0` and `EchoAgent_1` appear with distinct socket addresses — confirming that the same message is received twice, once directly and once relayed:

```text
2025-07-23 13:01:14.746 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:53195) has now 2 messages stored.   # SendAgent_0
2025-07-23 13:01:14.747 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:53191).
2025-07-23 13:01:14.749 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:53191) has now 1 messages stored.   # EchoAgent_1
```

Note how the **message count for `RecvAgent_0` jumps**:

* `SendAgent_0` has already delivered two messages
* `EchoAgent_1` has just started relaying the first one
  → This lag confirms the **1-second buffering delay** in `EchoAgent_1`

This confirms that `EchoAgent_1` functions as a passive relay with modular validation logic — a useful pattern for testing layered pipelines, delayed delivery, or gossip-like behavior.
