# `EchoAgent_2`

An advanced echo agent that buffers, validates, and signs messages before re-sending them with a 1-second delay. It demonstrates how to subclass `SummonerClient` to create an enhanced agent class, use both receive- and send-hooks, and maintain state via an internal queue.

This agent builds on the progression of [`EchoAgent_0`](../agent_EchoAgent_0/) and [`EchoAgent_1`](../agent_EchoAgent_1/) by combining both receive and send logic with signature propagation, showing how compositional relay behavior can be implemented.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.  
2. `MyAgent`, a subclass of `SummonerClient`, loads a persistent UUID (`my_id`) from `id.json`.  
3. Incoming messages invoke the receive-hook (`@agent.hook(Direction.RECEIVE)`):
   - If it's a string starting with `"Warning:"`, logs a warning and drops the message.  
   - If it's not a dict with `"remote_addr"` and `"content"`, logs:
     ```
     [hook:recv] missing address/content
     ```
     and drops it.  
   - Otherwise, logs:
     ```
     [hook:recv] <addr> passed validation
     ```
     and forwards the message to the receive handler.  
4. The receive handler (`@agent.receive(route="")`) serializes `content`, enqueues it into `message_buffer`, and logs:
     ```
     Buffered message from:(SocketAddress=<addr>).
     ```
5. Before sending, the send-hook (`@agent.hook(Direction.SEND)`) logs:
     ```
     [hook:send] sign <first-5-chars-of-UUID>
     ```
   It wraps strings into `{"message":...}`, adds `{"from": my_id}`, and forwards the message to the send handler.  
6. The send handler (`@agent.send(route="")`) awaits `message_buffer.get()`, sleeps 1 second, and returns the signed content.  
7. Steps 3-6 repeat until the client is stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                                | Description                                                   |
|----------------------------------------|---------------------------------------------------------------|
| `class MyAgent(SummonerClient)`        | Subclasses `SummonerClient` to load a persistent UUID         |
| `SummonerClient(name=...)`                  | Instantiates and manages the agent                            |
| `@agent.hook(Direction.RECEIVE)`      | Validates or drops incoming messages before main handling     |
| `@agent.hook(Direction.SEND)`         | Signs outgoing messages by adding a `from` field with UUID    |
| `@agent.receive(route=...)`           | Buffers validated messages into the queue                     |
| `@agent.send(route=...)`              | Emits buffered, signed messages periodically                  |
| `agent.logger`                        | Logs hook activity, buffering, and send lifecycle events      |
| `agent.loop.run_until_complete(...)`  | Runs the `setup` coroutine to initialize the message queue    |
| `agent.run(...)`                  | Connects to the server and starts the asyncio event loop  |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.


Then run the agent:

```bash
python agents/agent_EchoAgent_2/agent.py
```


## Simulation Scenarios

This scenario shows how `EchoAgent_2` rescues structurally invalid messages from [`SendAgent_0`](../agent_SendAgent_0/) so that [RecvAgent_2](../agent_RecvAgent_2/) can accept them.

```bash
# Terminal 1: server
python server.py

# Terminal 2: validating receiver (RecvAgent_2)
python agents/agent_RecvAgent_2/agent.py

# Terminal 3: raw sender (SendAgent_0)
python agents/agent_SendAgent_0/agent.py

# Terminal 4: echo relay (EchoAgent_2)
python agents/agent_EchoAgent_2/agent.py
```

* **`RecvAgent_2`** initially rejects and bans `SendAgent_0` after repeated invalid messages:

  ```
  [hook:recv] 127.0.0.1:49577 invalid -> checking if ban is required...
  ...
  [hook:recv] 127.0.0.1:49577 has been banned
  ```
* Once **`EchoAgent_2`** runs, it buffers and re-sends each message with a valid `"from"` field:

    ```
    [hook:recv] 127.0.0.1:49577 passed validation
    Buffered message from:(SocketAddress=127.0.0.1:49577).
    [hook:send] sign 6fb3f
    ```

* **`RecvAgent_2`** then accepts and stores the relayed messages:

  ```
  [hook:recv] 127.0.0.1:53195 valid, id=6fb3f...
  Received message from Agent @(id=6fb3f...)
  Agent @(id=6fb3f...) has now 1 messages stored.
  ```

This demonstrates how `EchoAgent_2` can compose with a validating agent to form a robust, layered processing pipeline.
