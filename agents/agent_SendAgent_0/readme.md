# ChatAgent0

This agent serves as a minimal example of a client that periodically emits messages to the server using the `@send` decorator from the Summoner SDK. It demonstrates how to register a sending route and send static content at regular intervals.

## Behavior

Once launched, the agent connects to the server and emits the message `"Hello Server!"` every second on the route `"custom_send"`. The server logs each message along with the client's address.

**Example server log output:**
```
🔌 127.0.0.1:64851 connected.
{"addr":"127.0.0.1:64851","content":"Hello Server!"}
{"addr":"127.0.0.1:64851","content":"Hello Server!"}
...
⚠️ 127.0.0.1:64851 disconnected gracefully.
```

The agent stops when the script is interrupted.


## SDK Features Used

- `SummonerClient`: creates and manages the agent instance.
- `@send(route=...)`: registers a function that emits a message periodically.
- `agent.run(...)`: connects the client to the server and initiates the async lifecycle.


## How to Run

First, ensure the Summoner server is running:

```bash
python server.py
````

Then run the agent:

```bash
python agents/agent_SendAgent_0/agent.py
```

## Simulation Scenarios (Optional)

> This section can be used to describe multi-agent tests involving `ChatAgent`, or to compare behaviors when run alongside echo, relay, or throttling agents.

*(Not populated yet)*

