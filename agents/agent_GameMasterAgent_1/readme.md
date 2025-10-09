## `GameMasterAgent_1`

An authoritative simulator for a larger world with clustered spawning near center using a golden-angle ring. It consumes player ticks and broadcasts `world_state` at a regular cadence.

> [!NOTE]
> Works well with `GamePlayerAgent_1` or `GamePlayerAgent_2` for camera follow and seeded visuals.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. Start a fixed time step simulation loop at about 60 Hz on a large map.
2. `@client.receive("gm/tick")`

   * Creates a player on first contact and places it near the center ring with small jitter.
   * Updates pressed keys for that player.
3. Simulation step

   * Applies diagonal-normalized velocity and clamps to bounds.
4. `@client.send("gm/reply")` every 50 ms

   * Publishes authoritative `world_state`.
5. `@client.hook(Direction.RECEIVE)`

   * Normalizes envelopes to a consistent dict payload.

</details>

## SDK Features Used

| Feature                               | Description                           |
| ------------------------------------- | ------------------------------------- |
| `SummonerClient(name=...)`            | Instantiates and manages the agent    |
| `@client.receive("gm/tick")`          | Ingests player input ticks            |
| `@client.send("gm/reply")`            | Periodic world snapshot broadcast     |
| `@client.hook(Direction.RECEIVE)`     | Envelope normalization                |
| `client.loop.create_task(sim_loop())` | Concurrent simulation loop            |
| `client.run(host, port, config_path)` | Client connection and loop management |

## How to Run

```bash
# Terminal 1 (server)
python server.py --config configs/server_config_MMO.json

# Terminal 2 (game master)
python agents/agent_GameMasterAgent_1/agent.py

# Terminal 3 (player 1)
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id alice

# Terminal 4 (player 2)
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id bob
```

With seeded visuals:

```bash
# Terminal 1 (server)
python server.py --config configs/server_config_MMO.json

# Terminal 2 (game master)
python agents/agent_GameMasterAgent_1/agent.py

# Terminal 3 (player 1)
python agents/agent_GamePlayerAgent_2/agent.py --avatar wizard.png --id alice --seed lava

# Terminal 4 (player 2)
python agents/agent_GamePlayerAgent_2/agent.py --avatar wizard.png --id bob --seed lava
```