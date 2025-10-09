# `GamePlayerAgent_2`

A client with seeded pixel-art grass and an LRU tile cache for performance. The seed is persisted for reproducible visuals. Same networking pattern as `GamePlayerAgent_1`.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. Load or create a persistent player ID from `--id`.
2. Load or create a persistent world seed from `--seed` or `world_seed.txt`.
3. Start a background Summoner client thread.
4. `@client.send("gm/tick")` every 50 ms

   * Publishes current `keys` with PID stamped by a send hook.
5. `@client.receive("gm/reply")`

   * Updates the shared snapshot with `bounds`, `players`, `ts`.
6. Pygame UI loop

   * Renders seeded grass using a Bayer-dithered two-shade tile.
   * Fetches tiles from an LRU cache keyed by `(seed, ix, iy)`.
   * Optional PNG avatar rendered for self if provided.
7. Hooks

   * `@client.hook(Direction.RECEIVE)` normalization.
   * `@client.hook(Direction.SEND)` PID injection.

</details>

## SDK Features Used

| Feature                            | Description                         |
| ---------------------------------- | ----------------------------------- |
| `SummonerClient(name=...)`         | Instantiates the client             |
| `@client.send("gm/tick")`          | Periodic input publication          |
| `@client.receive("gm/reply")`      | Consumes world snapshots            |
| `@client.hook(Direction.RECEIVE)`  | Envelope normalization              |
| `@client.hook(Direction.SEND)`     | PID injection                       |
| `client.run(..., config_dict=...)` | Optional programmatic configuration |

## How to Run

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