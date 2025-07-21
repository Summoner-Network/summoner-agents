# `SendAgent_1`

This agent builds on [`SendAgent_0`](../agent_SendAgent_0/) by adding a pre-send hook that "signs" each message with a unique client identifier. It demonstrates how to use the `@hook` decorator to inspect and transform outgoing payloads before they're dispatched.

## Behavior

1. On startup, the agent generates a UUID (stored in `my_id`).  
2. Every second, the `custom_send` coroutine returns the string `"Hello Server!"`.  
3. Before sending, the `sign` hook is invoked:
   - Logs a message like `[hook:send] sign 8b57d`  
   - Wraps the string into a dict `{ "message": "...", "from": "<my_id>" }`  
4. The annotated payload is sent to the server over the default route.  
5. The server logs each incoming JSON in the terminal and/or in the `logs/SendAgent_1.log` file
6. The agent continues until manually stopped (e.g. Ctrl+C).


## SDK Features Used

| Feature                                   | Description                                                       |
|-------------------------------------------|-------------------------------------------------------------------|
| `SummonerClient(...)`                     | Creates and manages the agent instance                            |
| `@client.hook(direction=Direction.SEND)`  | Intercepts and transforms outgoing messages                       |
| `client.logger`                           | Built-in logger for recording runtime events and debugging info   |
| `@client.send(route=...)`                 | Registers an async function that emits a message periodically     |
| `client.run(...)`                         | Connects the client to the server and initiates the async lifecycle |


## How to Run

First, ensure the Summoner server is running:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.


Then run the agent:

```bash
python agents/agent_SendAgent_1/agent.py
```

## Simulation Scenarios (Optional)

*(Not populated yet)*

