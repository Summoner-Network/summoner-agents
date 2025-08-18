# `ChatAgent_1`

A chat agent built on [`ChatAgent_0`](../agent_ChatAgent_0). It retains the **two input modes** (single-line or multi-line via `multi_ainput` in [`multi_ainput.py`](./multi_ainput.py)) and extends the interface with:

* **Remote commands** – received from another agent to **travel** between servers or **quit** (executed on receipt, not printed).
* **Self-commands** – entered locally at the prompt to perform the same actions (executed immediately without sending a payload).

This agent demonstrates an enhanced user interface for interacting with `SummonerClient`, enabling both conversation and command control across agents and servers.


## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses the CLI argument `--multiline 0|1` to select the input mode.

   * Default is one-line input using `ainput("> ")`.

2. When a message arrives (`@client.receive(route="")`), the handler:

   * extracts `content` when the inbound payload is a dict holding a `"content"` field, otherwise uses the raw message,
   * checks for **remote commands** sent by a peer:

     * `/travel` → `client.travel_to(host="testnet.summoner.org", port=8888)`
     * `/go_home` → `client.travel_to(host=client.default_host, port=client.default_port)`
     * `/quit` → `client.quit()`
       *(commands are executed and not printed)*
   * if not a command, prints `[From server]` when the text starts with `"Warning:"`, or `[Received]` otherwise,
   * redraws a primary prompt indicator `> ` on the next line.

3. When sending (`@client.send(route="")`), the agent:

   * uses `multi_ainput("> ", "~ ", "\\")` if `--multiline 1` to accept multi-line input,

     * a trailing backslash `\` continues on the next line, the backslash is removed after Enter,
     * the rewrite accounts for wrapped lines and wide Unicode **via `wcwidth`**,
     * one string is returned with real newline characters between lines,
   * or, if `--multiline 0`, reads a single line with `ainput("> ")`,
   * before sending, checks for **local/self commands** typed at the prompt:

     * `/self.travel` → travel to the testnet and **do not send a payload**
     * `/self.go_home` → return to `client.default_host:client.default_port` and **do not send a payload**
     * `/self.quit` → terminate this client and **do not send a payload**
   * if no self-command is detected, sends the typed content to the server as-is (this includes sending `/travel` to control a remote agent).

4. To run continuously, the client calls `client.run(...)` and drives the async receive and send coroutines until interrupted.

</details>

## SDK Features Used

| Feature                                       | Description                                                   |
| --------------------------------------------- | ------------------------------------------------------------- |
| `SummonerClient(name=...)`                    | Instantiates and manages the agent context                    |
| `@client.receive(route="")`                   | Handles inbound messages and executes remote commands         |
| `@client.send(route="")`                      | Reads user input, handles self-commands, and returns payloads |
| `client.travel_to(host, port)`                | Moves the connected client to another server                  |
| `client.default_host` / `client.default_port` | Default address set by `client.run(...)`, used by `/go_home`  |
| `client.quit()`                               | Terminates the client cleanly                                 |
| `client.run(host, port, config_path)`         | Connects to the server and starts the asyncio event loop      |

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
  python agents/agent_ChatAgent_1/agent.py
  ```

* To use multi-line input, end a line with a trailing backslash to continue on the next line. The backslash is removed from the echo and a continuation prompt `~ ` appears.

  ```bash
  # Multi-line input with backslash continuation (1 = enabled, 0 = disabled)
  python agents/agent_ChatAgent_1/agent.py --multiline 1
  ```


## Simulation Scenarios

This scenario runs one local server and **two `ChatAgent_1` instances**. You will use the multiline agent to remotely control the single-line agent: first send it to the testnet, then follow it, then bring it back home, and finally quit both.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ChatAgent_1, multiline controller)
python agents/agent_ChatAgent_1/agent.py --multiline 1

# Terminal 3 (ChatAgent_1, single-line target)
python agents/agent_ChatAgent_1/agent.py
```

**Step 1. Say hello from Terminal 2 and confirm Terminal 3 receives it.**
You start on the local server (`127.0.0.1:8888`). Send a greeting from the multiline agent; the single-line agent sees it.

**Terminal 2 (controller)**

```
python agents/agent_ChatAgent_1/agent.py --multiline 1
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 14:05:43.731 - ChatAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello
~ How are you?
```

**Terminal 3 (target)**

```
python agents/agent_ChatAgent_1/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 14:05:44.939 - ChatAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello
How are you?
>
```

**Step 2. From Terminal 2, remotely send Terminal 3 to testnet, then follow it.**
Type `/travel` in Terminal 2 to command the **other** agent to move. Terminal 3 disconnects from local and reconnects to `testnet.summoner.org:8888`.
Then type `/self.travel` in Terminal 2 so the controller follows to testnet.

**Terminal 2 (controller)**

```
> /travel
> Are still there?
> /self.travel
2025-08-18 14:06:32.945 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:06:32.983 - ChatAgent_1 - INFO - Connected to server @(host=testnet.summoner.org, port=8888)
> Hey are you there?
```

> [!NOTE]
> The line `Are still there?` is sent while the target is already on testnet and the controller is still local, so it won’t be seen by the target. After `/self.travel`, both agents are again on the same server (testnet), and messages sync up.

**Terminal 3 (target)**

```
> 2025-08-18 14:06:07.458 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 14:06:07.459 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:06:07.649 - ChatAgent_1 - INFO - Connected to server @(host=testnet.summoner.org, port=8888)
[Received] Hey are you there?
>
```

**Step 3. From Terminal 2, remotely send Terminal 3 back home, then follow it home.**
Type `/go_home` in Terminal 2 to make the **other** agent return to its `default_host:default_port` (set by `client.run(...)`).
Then type `/self.go_home` in Terminal 2 to bring the controller back to local as well.

**Terminal 2 (controller)**

```
> /go_home
> Did you leave?
> /self.go_home
2025-08-18 14:07:06.291 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:07:06.294 - ChatAgent_1 - INFO - Connected to server @(host=localhost, port=8888)
> Back!
```

**Terminal 3 (target)**

```
> 2025-08-18 14:06:51.212 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 14:06:51.214 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:06:51.216 - ChatAgent_1 - INFO - Connected to server @(host=localhost, port=8888)
[Received] Back!
>
```

**Step 4. From Terminal 2, remotely quit Terminal 3, then quit Terminal 2 locally.**
Use `/quit` to stop the **other** agent. Then use `/self.quit` to stop the controller itself.

**Terminal 2 (controller)**

```
> /quit
> /self.quit
2025-08-18 14:07:23.563 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:07:23.565 - ChatAgent_1 - INFO - Client exited cleanly.
```

**Terminal 3 (target)**

```
> 2025-08-18 14:07:19.686 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 14:07:19.688 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 14:07:19.690 - ChatAgent_1 - INFO - Client exited cleanly.
```

> [!NOTE]
> The testnet address `testnet.summoner.org:8888` must be reachable from your environment. If not, replace it with a reachable host/port in the code or via configuration.
