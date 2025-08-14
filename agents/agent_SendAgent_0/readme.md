# `SendAgent_0`

This agent serves as a minimal example of a client that periodically emits messages to the server using the `@send` decorator from the Summoner SDK. It demonstrates how to register a sending route and send static content at regular intervals.

## Behavior

Once launched, the agent connects to the server and emits the message `"Hello Server!"` every second. The agent stops when the script is interrupted.


## SDK Features Used

| Feature                         | Description                                                      |
|---------------------------------|------------------------------------------------------------------|
| `SummonerClient(name=...)`           | Creates and manages the agent instance                           |
| `@client.send(route=...)`       | Registers a function that emits a message periodically           |
| `client.run(...)`               | Connects the client to the server and initiates the async lifecycle |


## How to Run

First, ensure the Summoner server is running:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then run the agent:

```bash
python agents/agent_SendAgent_0/agent.py
```

## Simulation Scenarios

*(Not populated yet)*

