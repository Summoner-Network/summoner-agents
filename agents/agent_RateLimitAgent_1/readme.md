# `RateLimitAgent_1`

A variant of [`RateLimitAgent_0`](../agent_RateLimitAgent_0/) that uses `multi=True` to emit batches of messages at once, allowing it to hit the server’s rate limit in just two sends. It builds on the send/receive tracking of `RateLimitAgent_0` while demonstrating bulk emission.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, a global `tracker` dict and an `asyncio.Lock` (`tracker_lock`) are initialized.  
2. The receive handler (`@client.receive(route="defenses")`):
   - Prints incoming defense messages.  
   - If a message starts with `"Warning:"`, increments `tracker["defended"]`.  
3. The send handler (`@client.send(route="attack", multi=True)`):
   - Sleeps for 0.1 s between batches.  
   - Constructs a large Lorem ipsum string and updates:
     - `tracker["count"]`  
     - Elapsed time since start  
     - `tracker["defended"]`  
   - Returns a list of 50 identical payload dicts (`[msg] * 50`).  
4. Steps 2–3 repeat until the client is stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                                   | Description                                                      |
|-------------------------------------------|------------------------------------------------------------------|
| `SummonerClient(name=...)`                | Instantiates and manages the agent                               |
| `@client.receive(route="defenses")`       | Handles server backpressure warnings                             |
| `@client.send(route="attack", multi=True)` | Emits multiple messages per invocation                          |
| `client.run(...)`                         | Connects to the server and starts the asyncio event loop         |

## How to Run

First, start the Summoner server, ideally with the backpressure config to trigger rate limits quickly:
```bash
python server.py --config configs/server_config_backpressure.json
```

> [!TIP]
> To test the default rate limit used in `configs/server_config.json`, omit the custom config.

Then, launch the rate-limit agent:

```bash
python agents/agent_RateLimitAgent_1/agent.py
```

## Simulation Scenarios

Run against a server set to 100 msgs/min:

```bash
# Terminal 1: server
python server.py --config configs/server_config_backpressure.json

# Terminal 2: batch-send agent
python agents/agent_RateLimitAgent_1/agent.py
```

* On the **server** terminal, you will immediately see 50 messages with `count: 1` and then 50 with `count: 2`, reaching the 100 msgs/min limit:

  ```
  ... "count": 1 ...  (50×)
  ... "count": 2 ...  (50×)
  ```
* The server stops relaying after the second batch:

  ```
  2025-07-23 18:35:30.693 - MyServer - INFO - {"...,\"count\":2,..."}
  2025-07-23 18:35:30.694 - MyServer - INFO - {"...,\"count\":2,..."}
  ```
* On the **agent** terminal, you will see backpressure warnings as they arrive:

  ```
  Warning: You are sending messages too quickly. Please slow down.

  Warning: You are sending messages too quickly. Please slow down.
  ...
  ```

This demonstrates how bulk emission with `multi=True` can be used to rapidly test server rate-limit defenses.

> [!NOTE] 
> Compare with [`RateLimitAgent_0`](../agent_RateLimitAgent_0/) to see how `multi=True` accelerates rate-limit triggering.
