## `GamePlayerAgent_0`

A minimal client that polls the keyboard, sends input ticks at 20 Hz, and renders the server’s `world_state` in a fixed window.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. Start a background Summoner client thread.
2. `@client.send("gm/tick")` every 50 ms

   * Captures WASD or arrow keys into `keys` and stamps `ts`.
3. `@client.receive("gm/reply")`

   * Updates a shared snapshot with `bounds`, `players`, and `ts`.
4. Pygame UI loop

   * Draws circles for players and a simple HUD with PID and player count.
5. Hooks

   * `@client.hook(Direction.RECEIVE)` normalizes envelopes.
   * `@client.hook(Direction.SEND)` injects `pid` if missing.

</details>

## SDK Features Used

| Feature                               | Description                |
| ------------------------------------- | -------------------------- |
| `SummonerClient(name=...)`            | Instantiates the client    |
| `@client.send("gm/tick")`             | Periodic input publication |
| `@client.receive("gm/reply")`         | Consumes world snapshots   |
| `@client.hook(Direction.RECEIVE)`     | Envelope normalization     |
| `@client.hook(Direction.SEND)`        | PID injection              |
| `client.run(host, port, config_path)` | Starts the networking loop |

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