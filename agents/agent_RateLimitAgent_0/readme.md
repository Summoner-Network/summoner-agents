# `RateLimitAgent_0`

A simple test agent that sends large payloads at a steady rate, tracks send counts and defenses, and demonstrates how to handle server backpressure via a receive-hook on the `"defenses"` route.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, a global `tracker` dict and an `asyncio.Lock` (`tracker_lock`) are initialized.  
2. The receive handler (`@client.receive(route="defenses")`):
   - Prints any incoming defense message (e.g. server warnings).  
   - If the message starts with `"Warning:"`, increments `tracker["defended"]`.  
3. The send handler (`@client.send(route="attack")`):
   - Waits 0.1 s between sends.  
   - Builds a large Lorem ipsum string.  
   - Increments `tracker["count"]`, records elapsed time since start, and includes `tracker["defended"]` in each payload.  
   - Returns a dict:
     ```json
     {
       "message": "...", 
       "count": <n>, 
       "time": "<seconds-since-start>", 
       "defended": <defended-count>
     }
     ```
4. The agent runs continuously until stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                             | Description                                                              |
|-------------------------------------|--------------------------------------------------------------------------|
| `SummonerClient(name=...)`          | Instantiates and manages the agent                                       |
| `@client.receive(route="defenses")` | Handles server defense messages (backpressure warnings)                   |
| `@client.send(route="attack")`      | Sends attack messages on a fixed schedule                                |
| `client.run(...)`                   | Connects to the server and starts the asyncio event loop                 |

## How to Run

1. **Start the Summoner server with backpressure**  
   Use the backpressure config to trigger rate limits quickly:
   ```bash
   python server.py --config configs/server_config_backpressure.json
   ```

2. **Launch the rate-limit agent**

   ```bash
   python agents/agent_RateLimitAgent_0/agent.py
   ```

> [!TIP]
> To test the default rate limit used in `configs/server_config.json`, omit the custom config.

## Simulation Scenarios

Run the agent against a server set to 100 messages/min:

```bash
# Terminal 1: server with rate limit of 100 msgs/min
python server.py --config configs/server_config_backpressure.json

# Terminal 2: rate-limit agent
python agents/agent_RateLimitAgent_0/agent.py
```

* On the **server** terminal, you will see rapid attack payloads logged until the limit is exceeded:

  ```
  2025-07-23 18:15:38.032 - MyServer - INFO - {"addr":"127.0.0.1:56358","content":"{...\"count\":58,...}"}
  ...
  2025-07-23 18:15:42.314 - MyServer - INFO - {"addr":"127.0.0.1:56358","content":"{...\"count\":100,...}"}
  ```
* Once the rate limit is hit, the server sends backpressure warnings on `defenses`:

  ```
  Warning: You are sending messages too quickly. Please slow down.

  Warning: You are sending messages too quickly. Please slow down.
  
  ...
  ```
* On the **agent** terminal, you will see no direct output except these warnings as they arrive.

After one minute, the server will resume accepting messages and you will observe new payloads arriving again.

This demonstrates how to combine send scheduling, receive-hooks, and state tracking to test and respond to server-enforced rate limits.

