# `ChatAgent_0`

A minimal chat agent that supports two input modes: a standard **single-line prompt** or a **multi-line prompt** using a trailing backslash for continuation (via the `multi_ainput` helper in [`multi_ainput.py`](./multi_ainput.py)). This agent provides a simple user interface for a send/receive pipeline with `SummonerClient`.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses the CLI argument `--multiline 0|1` to select the input mode.

   * Default is one-line input using `ainput("> ")`.

2. When a message arrives (`@client.receive(route="custom_receive")`), the handler:

   * extracts `content` when the inbound payload is a dict holding a `"content"` field, otherwise uses the raw message,
   * prints `[From server]` when the text starts with `"Warning:"`, or `[Received]` otherwise,
   * redraws a primary prompt indicator `> ` on the next line.

3. When sending (`@client.send(route="custom_send")`), the agent:

   * uses `multi_ainput("> ", "~ ", "\\")` if `--multiline 1` to accept multi-line input,
   * treats a trailing backslash `\` as a continuation signal and removes it from the echoed line after Enter,
   * accounts for wrapped lines and wide Unicode when rewriting the line,
   * returns one string with real newline characters between lines,
   * or, if `--multiline 0`, reads a single line with `ainput("> ")`.

4. To run continuously, the client calls `client.run(...)` and drives the async receive and send coroutines until interrupted.

</details>

## SDK Features Used

| Feature                      | Description                                                   |
| ---------------------------- | ------------------------------------------------------------- |
| `SummonerClient(name=...)`   | Instantiates and manages the agent context                    |
| `@client.receive(route=...)` | Handles inbound messages and prints a tagged display          |
| `@client.send(route=...)`    | Reads user input (one-line or multi-line) and returns payload |
| `client.run(...)`            | Connects to the server and starts the asyncio event loop      |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then, run the chat agent. You can choose one-line or multi-line input.

* To use one-line input, press Enter to send immediately. The backslash has no special meaning in this mode.

  ```bash
  # One-line input (default)
  python agents/agent_ChatAgent_0/agent.py
  ```

* To use multi-line input, end a line with a trailing backslash to continue on the next line. The backslash is removed from the echo and a continuation prompt `~ ` appears.

  ```bash
  # Multi-line input with backslash continuation (1 = enabled, 0 = disabled)
  python agents/agent_ChatAgent_0/agent.py --multiline 1
  ```

## Simulation Scenarios

This scenario runs one server and two agents so you can compare multi-line and single-line behavior side by side.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ChatAgent_0, multiline)
python agents/agent_ChatAgent_0/agent.py --multiline 1

# Terminal 3 (ChatAgent_0, single line)
python agents/agent_ChatAgent_0/agent.py
```

**Step 1. Compose the first line with continuation in Terminal 2, then press Enter.**
The agent removes the trailing backslash from the echoed line and prepares a continuation line.

```
python agents/agent_ChatAgent_0/agent.py --multiline 1
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 13:39:14.754 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello\
```

After Enter, the terminal rewrites the line without the backslash and shows the continuation prompt:

```
> Hello
~ 
```

**Step 2. Type the continuation in Terminal 2 and press Enter.**
The agent sends one payload that contains a single string with a real newline between the two lines.

```
> Hello
~ How are you?
```

**Step 3. Respond from Terminal 3 using single-line mode.**
In single-line mode, Enter sends immediately and the backslash is just a normal character.

```
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 13:39:22.108 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello
How are you?
> Good! Thanks you
```

**Step 4. Try a backslash in single-line mode to see the difference.**
Since backslash has no special meaning here, pressing Enter sends the line as is. The receiving side will see the backslash in the content.

```
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 13:39:22.108 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello
How are you?
> Good! Thanks you
> How about you?\
> All good?                        
> 
```

On the multi-line side (Terminal 2), those single-line messages arrive exactly as sent, including the literal backslash:

```
python agents/agent_ChatAgent_0/agent.py --multiline 1
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 13:39:14.754 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello
~ How are you?
[Received] Good! Thanks you
[Received] How about you?\
[Received] All good?
> 
```
