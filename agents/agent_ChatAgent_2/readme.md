# `ChatAgent_2`

A chat agent built on [`ChatAgent_0`](../agent_ChatAgent_0) and [`ChatAgent_1`](../agent_ChatAgent_1) that **activates the automaton/flow engine** to route incoming messages by **state**. It retains the two input modes (single-line or multi-line via [`multi_ainput`](./multi_ainput.py)) and introduces a simple **state machine**:

* **`opened`** — remote commands are executed on receipt (`/travel`, `/go_home`, `/quit`).
* **`locked`** — remote commands are **ignored** and shown as plain messages.

State is uploaded via `@client.upload_states()` and toggled locally with `/self.open` and `/self.lock`.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses `--multiline 0|1` to select input mode (default is one-line using `ainput("> ")`).
2. To enable the automaton, it calls `client.flow().activate()`, which makes **routes** sensitive to the uploaded state.
3. The handler `@client.upload_states()` returns the **current state** (`"opened"` or `"locked"`), which the flow engine uses to select which `@client.receive(route=...)` is active.
   
   > 📝 **Note:**
   > State reads/writes are guarded with `asyncio.Lock` for consistent orchestration.

4. When a message arrives:

   * If the current state is **`opened`**, the `route="opened"` receiver runs and executes remote commands (`/travel`, `/go_home`, `/quit`) or prints the message.
   * If the current state is **`locked`**, the `route="locked"` receiver runs and **does not** execute commands; it only prints the message.
5. When sending (`@client.send(route="any_state")`), the agent:

   * uses `multi_ainput("> ", "~ ", "\\")` if `--multiline 1` (backslash continuation with echo cleanup via `wcwidth`), or a single `ainput("> ")` line otherwise,
   * checks for **self-commands** that act locally and do not send payloads:

     * `/self.travel`, `/self.go_home`, `/self.quit` — same actions as in `ChatAgent_1`,
     * `/self.lock`, `/self.open` — toggle the agent state.
   * if no self-command is detected, sends the content as a normal message (which can be a remote command for the other agent).
6. The loop runs via `client.run(...)` until interrupted.

</details>

## SDK Features Used

| Feature                                | Description                                                                   |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| `SummonerClient(name=...)`             | Instantiates and manages the agent context                                    |
| `client.flow()` / `flow().activate()`  | Turns on the automaton so that `@receive(route=...)` is selected by uploaded state |
| `@client.upload_states()`              | Uploads the **current state** to drive which receivers are active             |
| `@client.receive(route="opened")`      | Executes remote commands; prints other messages                               |
| `@client.receive(route="locked")`      | Ignores commands; prints messages only                                        |
| `@client.send(route="any_state")`      | Reads input, handles self-commands, and returns payloads                      |
| `client.travel_to(host, port)`         | Moves the client to another server                                            |
| `client.default_host` / `default_port` | Populated by `client.run(...)`, used by `/go_home`                            |
| `client.quit()`                        | Terminates the client cleanly                                                 |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then, run two `ChatAgent_2` instances so you can see **state-driven behavior** across agents:

```bash
# Terminal 2 (multiline)
python agents/agent_ChatAgent_2/agent.py --multiline 1

# Terminal 3 (single-line)
python agents/agent_ChatAgent_2/agent.py
```

## Simulation Scenarios

This scenario demonstrates the **stateful automaton**: you first verify that **remote travel** works, then you **lock** the target so remote commands are ignored, then you **open** it again, and finally you **quit** the agents.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ChatAgent_2, multiline controller)
python agents/agent_ChatAgent_2/agent.py --multiline 1

# Terminal 3 (ChatAgent_2, single-line target)
python agents/agent_ChatAgent_2/agent.py
```

**Step 1. Exchange a greeting to confirm both agents are on localhost.**
The controller (Terminal 2) sends a multi-line message; the target (Terminal 3) receives it and replies.

**Terminal 2 (controller)**

```
python agents/agent_ChatAgent_2/agent.py --multiline 1
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 15:07:22.442 - ChatAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[opened]> Hello
~ How are you?
```

**Terminal 3 (target)**

```
python agents/agent_ChatAgent_2/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-18 15:07:23.750 - ChatAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[opened]> 2025-08-18 15:07:31.747 - ChatAgent_1 - INFO - Use handler @(route='opened')
[Received] Hello
How are you?
[opened]> I am good thanks
```

**Step 2. Test remote travel, then follow.**
From Terminal 2, send `/travel` (remote command) so the **other** agent moves to testnet. Then use `/self.travel` to move the controller as well. Both reconnect to `testnet.summoner.org:8888`.

**Terminal 2 (controller)**

```
[opened]> /travel
[opened]> /self.travel
2025-08-18 15:08:06.447 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:08:06.495 - ChatAgent_1 - INFO - Connected to server @(host=testnet.summoner.org, port=8888)
```

**Terminal 3 (target)**

```
[opened]> 2025-08-18 15:07:58.934 - ChatAgent_1 - INFO - Use handler @(route='opened')
2025-08-18 15:07:58.936 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 15:07:58.938 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:07:59.025 - ChatAgent_1 - INFO - Connected to server @(host=testnet.summoner.org, port=8888)
```

**Step 3. Return home with a remote command, then follow.**
From Terminal 2, send `/go_home` (remote), then `/self.go_home` (local). Both return to localhost.

**Terminal 2 (controller)**

```
[opened]> /go_home
[opened]> /self.go_home
2025-08-18 15:08:19.769 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:08:19.774 - ChatAgent_1 - INFO - Connected to server @(host=localhost, port=8888)
[opened]> Hello!
```

**Terminal 3 (target)**

```
[opened]> 2025-08-18 15:08:12.200 - ChatAgent_1 - INFO - Use handler @(route='opened')
2025-08-18 15:08:12.201 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 15:08:12.202 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:08:12.205 - ChatAgent_1 - INFO - Connected to server @(host=localhost, port=8888)
[opened]> 2025-08-18 15:08:23.380 - ChatAgent_1 - INFO - Use handler @(route='opened')
[Received] Hello!
```

**Step 4. Lock the target and observe how commands are ignored.**
In Terminal 3, lock the agent with `/self.lock`. While **locked**, remote commands like `/travel` or `/quit` are not executed and appear as normal messages. This demonstrates that the **route `"locked"`** ignores command verbs.

**Terminal 3 (target)**

```
[opened]> /self.lock
[locked]> 2025-08-18 15:08:33.783 - ChatAgent_1 - INFO - Use handler @(route='locked')
```

**Terminal 2 (controller)**
Send a couple of remote commands; on the locked target they will be **displayed** rather than **executed**:

```
[opened]> /travel
[opened]> /quit
```

**Terminal 3 (target)**
Notice both commands show up as messages under the **locked** prompt:

```
[locked]> 2025-08-18 15:08:33.783 - ChatAgent_1 - INFO - Use handler @(route='locked')
[Received] /travel
[locked]> 2025-08-18 15:08:53.120 - ChatAgent_1 - INFO - Use handler @(route='locked')
[Received] /quit
```

**Step 5. Re-open the target and quit.**
Unlock with `/self.open` so the route switches back to `"opened"` (commands would execute again). Then finish the demo by quitting.

**Terminal 3 (target)**

```
[locked]> /self.open
[opened]> 2025-08-18 15:09:14.147 - ChatAgent_1 - INFO - Use handler @(route='opened')
2025-08-18 15:09:14.148 - ChatAgent_1 - INFO - Client about to disconnect...
2025-08-18 15:09:14.149 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:09:14.151 - ChatAgent_1 - INFO - Client exited cleanly.
```

**Terminal 2 (controller)**
Quit the controller locally:

```
[opened]> /self.quit
2025-08-18 15:09:19.282 - ChatAgent_1 - INFO - Disconnected from server.
2025-08-18 15:09:19.284 - ChatAgent_1 - INFO - Client exited cleanly.
```

> [!NOTE]
> The **automaton** behavior here is intentionally simple (`route == state`). The SDK supports richer logic where routes can match state **by structure**, not only equality, enabling more complex orchestration patterns.
