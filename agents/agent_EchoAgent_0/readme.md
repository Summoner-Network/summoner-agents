# `EchoAgent_0`

A simple echo agent that buffers incoming messages and re-sends them after a 1-second delay. It demonstrates how to use SummonerClient with a buffered send/receive pipeline based on an internal message queue.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.  
2. When a message arrives (`@client.receive`):
   - If it is a string starting with `"Warning:"`, logs a warning with the `"Warning:"` prefix replaced by `[From Server]`.  
   - If it is a dict with `"addr"` and `"content"`, serializes the content and enqueues it into `message_buffer`, logging:
     ```
     Buffered message from:(SocketAddress=<addr>)
     ```
3. The `@client.send` coroutine:
   - Awaits `message_buffer.get()` to retrieve the next message.  
   - Sleeps for 1 second to simulate delay.  
   - Returns the original content, which is sent back to the server.  
4. The cycle continues indefinitely until the client is stopped (e.g., Ctrl+C).

</details>

## SDK Features Used

| Feature                                | Description                                           |
|----------------------------------------|-------------------------------------------------------|
| `SummonerClient(...)`                  | Instantiates and manages the agent                    |
| `@client.receive(route=...)`           | Handles incoming messages                             |
| `@client.send(route=...)`              | Emits buffered messages periodically                  |
| `client.logger`                        | Logs runtime events and debugging information         |
| `client.loop.run_until_complete(...)`  | Initializes the agent's internal message queue        |
| `client.run(...)`                  | Connects to the server and starts the asyncio event loop   |


## How to Run

First, ensure the Summoner server is running:

   ```bash
   python server.py
   ```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then run the agent:

   ```bash
   python agents/agent_EchoAgent_0/agent.py
   ```

## Simulation Scenarios

### Scenario 1: Echo Chamber (Echo ↔ Echo loop)

This setup uses two `EchoAgent_0` instances and one `SendAgent_0`. It demonstrates how messages from the sender propagate and then continue bouncing between the echo agents.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (EchoAgent instance #1)
python agents/agent_EchoAgent_0/agent.py

# Terminal 3 (EchoAgent instance #2)
python agents/agent_EchoAgent_0/agent.py

# Terminal 4 (SendAgent_0, briefly)
python agents/agent_SendAgent_0/agent.py
```

To seed the echo, connect `SendAgent_0` briefly, then kill it (`Ctrl+C`). You will see:

```bash
# In SendAgent_0
[DEBUG] Loaded config from: configs/client_config.json
2025-07-23 01:38:19.005 - SendAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
^C2025-07-23 01:38:21.312 - SendAgent_0 - INFO - Client is shutting down...
```

Then, in each `EchoAgent_0` terminal, you will observe:

* At first, messages are buffered **from the address of `SendAgent_0`**
* Shortly after disconnection, new messages appear **from the other EchoAgent**, confirming that the two echo agents are bouncing messages between each other

Example:

```text
2025-07-23 01:38:15.023 - EchoAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2025-07-23 01:38:19.008 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:20.009 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58786).
2025-07-23 01:38:21.013 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58785).  <-- from the other EchoAgent
```

This validates that the echo agents function not just as forwarders, but also as reflexive relays once seeded.


### Scenario 2: Echo as a Relay (Send → Echo → Recv)

This setup places `EchoAgent_0` between a sender and a receiver, allowing messages to arrive twice: once directly from the sender, and once via the echo agent after a delay.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (EchoAgent)
python agents/agent_EchoAgent_0/agent.py

# Terminal 3 (RecvAgent_0)
python agents/agent_RecvAgent_0/agent.py

# Terminal 4 (SendAgent_0)
python agents/agent_SendAgent_0/agent.py
```

#### What Happens

* `SendAgent_0` sends messages directly to the server.
* `EchoAgent_0` receives these, buffers them, and re-sends them with a 1-second delay.
* `RecvAgent_0` receives **both**: first from `SendAgent_0`, then (a second later) from `EchoAgent_0`.

In `EchoAgent_0`, you will see:

```text
2025-07-23 01:40:26.264 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
2025-07-23 01:40:27.267 - EchoAgent_0 - INFO - Buffered message from:(SocketAddress=127.0.0.1:58831).
```

In `RecvAgent_0`, both agents show up with distinct socket addresses:

```text
2025-07-23 01:40:27.274 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58831) has now 2 messages stored.   # SendAgent_0
2025-07-23 01:40:27.275 - RecvAgent_0 - INFO - Received message from Client @(SocketAddress=127.0.0.1:58828).       
2025-07-23 01:40:27.277 - RecvAgent_0 - INFO - Client @(SocketAddress=127.0.0.1:58828) has now 1 messages stored.   # EchoAgent_0
```

Note how the **message count for `RecvAgent_0` jumps**:

* `SendAgent_0` has already delivered two messages
* `EchoAgent_0` has just started relaying the first one
  → This lag confirms the **1-second buffering delay** in `EchoAgent_0`

This setup illustrates how `EchoAgent_0` can act as a passive relay, introducing controlled delay and message duplication — useful for testing backpressure, resilience, or gossip - like protocols.