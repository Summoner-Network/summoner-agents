# `InputAgent` — README

A minimal input agent with two prompt modes—**single-line** and **multi-line**—that also **tries to parse JSON** before sending. If the user's input parses as JSON (after stripping newlines), it sends a Python object (e.g., `dict`, `list`); otherwise it sends the raw string. This makes it handy for interacting with agents that expect structured payloads.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses the CLI argument `--multiline 0|1` to select the input mode.

   * Default is one-line input using `ainput("> ")`.
   * Multi-line uses `multi_ainput("> ", "~ ", "\\")` with backslash continuation.

2. When a message arrives (`@client.receive(route="")`), the handler:

   * extracts `content` when the inbound payload is a dict with a `"content"` field; otherwise uses the raw message,
   * prints `[From server]` when the text starts with `"Warning:"`, or `[Received]` otherwise,
   * redraws a primary prompt indicator `> ` on the next line.

3. When sending (`@client.send(route="")`), the agent:

   * reads one line with `ainput("> ")` or multi-line with `multi_ainput("> ", "~ ", "\\")`,
   * attempts `json.loads(content.replace("\n", ""))`; if parsing succeeds, the resulting Python object is sent,
   * if parsing fails, the raw string is sent as-is.

4. The client runs continuously via `client.run(...)` until interrupted (Ctrl+C).

</details>

## SDK Features Used

| Feature                      | Description                                                                |
| ---------------------------- | -------------------------------------------------------------------------- |
| `SummonerClient(name=...)`   | Instantiates and manages the agent context                                 |
| `@client.receive(route=...)` | Handles inbound messages and prints a tagged display                       |
| `@client.send(route=...)`    | Reads user input, tries to parse JSON (fallback to string), and returns it |
| `client.run(...)`            | Connects to the server and starts the asyncio event loop                   |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then, run the input agent. You can choose one-line or multi-line input.

* **One-line input (default)** — press Enter to send immediately:

  ```bash
  python agents/agent_InputAgent/agent.py
  ```

* **Multi-line input** — end a line with a trailing backslash `\` to continue; a continuation prompt `~ ` appears:

  ```bash
  python agents/agent_InputAgent/agent.py --multiline 1
  ```

## Simulation Scenarios

This scenario runs one server and **two InputAgents** so you can compare modes and see JSON vs string behavior.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (InputAgent, multiline)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3 (InputAgent, single line)
python agents/agent_InputAgent/agent.py
```

**Scenario A — Multi-line composition with continuation (Terminal 2):**

```
python agents/agent_InputAgent/agent.py --multiline 1
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 13:39:14.754 - InputAgent - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello\
> 
```

After Enter, the agent rewrites the first line without the backslash and shows the continuation prompt:

```
> Hello
~ 
```

Type the continuation and press Enter:

```
> Hello
~ How are you?
```

This sends one string with a real newline. The other InputAgent prints:

```
[Received] Hello
How are you?
> 
```

**Scenario B — Sending JSON (Terminal 3):**
Type a valid JSON object on a single line:

```
> {"texts":["A","B","C"],"clustering":{"algo":"kmeans","k":2}}
```

InputAgent parses it and sends a Python dict. The receiving InputAgent displays the dict as text:

```
[Received] {'texts': ['A', 'B', 'C'], 'clustering': {'algo': 'kmeans', 'k': 2}}
> 
```

**Scenario C — Invalid JSON falls back to string (Terminal 3):**

```
> {not: "json"}
```

Parsing fails; the raw string is sent. The other InputAgent sees:

```
[Received] {not: "json"}
> 
```

