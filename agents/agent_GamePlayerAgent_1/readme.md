# `GamePlayerAgent_1`

A resizable client with camera follow, checkerboard grass tiles, optional avatar, and persistent identity. Same networking pattern as `GamePlayerAgent_0`.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. Load or create a persistent player ID from `--id` or a new `<id>.id` file.
2. Start a background Summoner client thread with default logger configuration.
3. `@client.send("gm/tick")` every 50 ms

   * Publishes current `keys` with PID injected by a send hook.
4. `@client.receive("gm/reply")`

   * Updates the shared snapshot of `bounds`, `players`, `ts`.
5. Pygame UI loop

   * Camera centers on the player when known.
   * Draws 2x2 checker grass tiles.
   * Optional PNG avatar rendered for self if provided.
6. Hooks

   * `@client.hook(Direction.RECEIVE)` normalize payloads.
   * `@client.hook(Direction.SEND)` stamp PID.

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
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id alice

# Terminal 4 (player 2)
python agents/agent_GamePlayerAgent_1/agent.py --avatar wizard.png --id bob
```