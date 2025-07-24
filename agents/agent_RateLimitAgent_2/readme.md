# `RateLimitAgent`

A variant of [`RateLimitAgent_1`](../agent_RateLimitAgent_1/) that sends triplets of messages (`multi=True`) and gracefully disconnects via `.quit()` after 10 backpressure warnings. It builds on the batch-send logic of `RateLimitAgent_1` by adding threshold-based shutdown behavior.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, initialize a global `tracker` dict and an `asyncio.Lock` (`tracker_lock`).  
2. Incoming defense messages (`@client.receive(route="defenses")`):
   - Print the message.  
   - If it starts with `"Warning:"`, increment `tracker["defended"]`.  
   - Once `tracker["defended"] >= 10`, print the final `tracker` and call `client.quit()` to disconnect.  
3. Outgoing attacks (`@client.send(route="attack", multi=True)`):
   - Sleep 0.1 s.  
   - Build a payload with:
     ```json
     {
       "message": "<long string>",
       "count": <tracker["count"]>,
       "time": "<seconds since start>",
       "defended": <tracker["defended"]>
     }
     ```
   - Increment `tracker["count"]` under lock.  
   - Return a list of 3 identical payloads (`[msg] * 3`).  
4. Steps 2–3 repeat automatically until `.quit()` stops the client.

</details>

## SDK Features Used

| Feature                                     | Description                                                          |
|---------------------------------------------|----------------------------------------------------------------------|
| `SummonerClient(name="RateLimitAgent")`     | Instantiates and manages the agent                                   |
| `@client.receive(route="defenses")`         | Handles backpressure warnings and triggers shutdown                  |
| `@client.send(route="attack", multi=True)`  | Emits batches of 3 messages per invocation                           |
| `client.quit()`                             | Gracefully quits the client protocol                                 |
| `client.run(...)`                           | Connects to the server and starts the asyncio event loop             |

## How to Run

First, start the Summoner server, ideally with the backpressure config to trigger rate limits quickly:
```bash
python server.py --config configs/server_config_backpressure.json
```

> [!TIP]
> To test the default rate limit used in `configs/server_config.json`, omit the custom config.

Then, launch the rate-limit agent:

```bash
python agents/agent_RateLimitAgent_2/agent.py
```

## Simulation Scenarios

```bash
# Terminal 1: server
python server.py --config configs/server_config_backpressure.json

# Terminal 2: batch-quit agent
python agents/agent_RateLimitAgent_2/agent.py
```

* On the **server** terminal, you will see batches of 3 payloads up to `count: 34`, then no further logs because the client quits:

  ```
  2025-07-23 18:43:37.584 - MyServer - INFO - {... "count": 34, ...}
  ```
* On the **agent** terminal, you will see 10 warnings, then the final tracker and disconnect logs:

  ```
  Warning: You are sending messages too quickly. Please slow down.

  Warning: You are sending messages too quickly. Please slow down.

  Warning: You are sending messages too quickly. Please slow down.
  ... (8×)

  {'count': 37, 'initial': 1753310614.1139462, 'defended': 10}
  2025-07-23 18:43:37.889 - RateLimitAgent - INFO - Client about to disconnect...
  2025-07-23 18:43:37.891 - RateLimitAgent - INFO - Disconnected from server.
  2025-07-23 18:43:37.892 - RateLimitAgent - INFO - Client exited cleanly.
  ```

This demonstrates how to combine bulk emission (`multi=True`), defense tracking, and `client.quit()` for self-regulating agents.
