## `GameMasterAgent_0`

An authoritative simulator for a shared 2D sandbox. It consumes player input ticks and periodically broadcasts a compact `world_state`. The server owns truth for positions.

> [!NOTE]
> Intended for quick local testing with two lightweight clients (`GamePlayerAgent_0`).

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. Start a fixed time step simulation loop at about 60 Hz.
2. `@client.receive("gm/tick")`

   * Ensures `pid` exists.
   * Creates a new in-memory player on first contact.
   * Updates pressed keys per player.
3. Simulation step

   * Translates keys to velocity with diagonal normalization.
   * Updates position and clamps to map bounds.
4. `@client.send("gm/reply")` every 50 ms

   * Publishes `world_state = {type, ts, bounds, players[]}`.
5. `@client.hook(Direction.RECEIVE)`

   * Normalizes Summoner envelopes to a plain dict payload.

</details>

## SDK Features Used

| Feature                               | Description                                            |
| ------------------------------------- | ------------------------------------------------------ |
| `SummonerClient(name=...)`            | Instantiates the agent and manages the event loop      |
| `@client.receive("gm/tick")`          | Ingests player input ticks                             |
| `@client.send("gm/reply")`            | Periodic broadcast of the authoritative world snapshot |
| `@client.hook(Direction.RECEIVE)`     | Envelope normalization for consistent payloads         |
| `client.loop.create_task(sim_loop())` | Runs the physics loop concurrently with networking     |
| `client.run(host, port, config_path)` | Connects to the server and starts the asyncio loop     |

## How to Run

```bash
# Terminal 1 (server)
python server.py --config configs/server_config_MMO.json

# Terminal 2 (game master)
python agents/agent_GameMasterAgent_0/agent.py

# Terminal 3 (player 1)
python agents/agent_GamePlayerAgent_0/agent.py

# Terminal 4 (player 2)
python agents/agent_GamePlayerAgent_0/agent.py
```
