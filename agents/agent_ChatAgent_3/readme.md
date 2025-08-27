# `ChatAgent_3`

A chat agent built on [`ChatAgent_2`](../agent_ChatAgent_2) that demonstrates the **automaton/flow** format with **explicit transitions** driven by receiver return values. It keeps the two input modes (single-line or multi-line via [`multi_ainput`](./multi_ainput.py)) and adds:

* **State-routed receivers** with routes like `opened --> locked` and `locked --> opened`.
* **Flow events** (`Move`, `Stay`) returned by receivers to evolve the automaton.
* **Remote control** to lock/unlock the agent (`/lock`, `/open <pw>`) and to travel/quit.
* **Self-commands** for the same actions locally (`/self.*`).

This agent shows how `SummonerClient` can orchestrate **which handler runs** based on the **current state** and the **event** returned by a receiver.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses `--multiline 0|1` to choose input mode (default is one-line via `ainput("> ")`).

2. To enable the automaton, it calls `client.flow().activate()`, declares an arrow style so strings like `opened --> locked` can be parsed, and calls `ready()` to compile patterns.

3. The agent uploads its **current state** with `@client.upload_states()`; the flow engine uses this to select which `@client.receive(route=...)` is active.

   > 📝 **Note:**
   > State reads/writes are guarded with `asyncio.Lock` for consistent orchestration.

4. When a message arrives:

   * If state is **`opened`**, the `route="opened --> locked"` receiver runs. It:

     * executes remote commands (`/travel`, `/go_home`, `/quit`),
     * **locks** on `/lock` by printing the next prompt `[locked]>` and returning `Move(Trigger.ok)`,
     * otherwise prints content and returns `Stay(Trigger.ok)`.
   * If state is **`locked`**, the `route="locked --> opened"` receiver runs. It:

     * **unlocks** only when it sees `/open HelloSummoner` (prints `[opened]>` and returns `Move(Trigger.ok)`),
     * otherwise prints content and returns `Stay(Trigger.ok)` (remote commands are shown, not executed).

5. After receivers return `Move`/`Stay`, the flow engine aggregates **possible next states** and calls `@client.download_states()`; this demo folds that list back to set `state` to `"opened"` or `"locked"`.

6. When sending (`@client.send(route="any_state")`), the prompt shows the **current state** (e.g., `[opened]>`). The agent:

   * reads input using [`multi_ainput`](./multi_ainput.py) (if `--multiline 1`) or `ainput` (if `--multiline 0`),
   * executes **self-commands** without sending a payload:

     * `/self.travel`, `/self.go_home`, `/self.quit`, `/self.lock`, `/self.open`,
   * otherwise sends the text as a normal message (which may be a **remote** command for the other agent).

7. The loop runs via `client.run(...)` until interrupted.

</details>
<br>

<details>
<summary><b>(Click to expand)</b> Quick command reference:</summary>
<br>

| Scope                       | Command               | Effect                                                            | Receiver state required           |
| --------------------------- | --------------------- | ----------------------------------------------------------------- | --------------------------------- |
| **Remote (received)**       | `/travel`             | Move the **other** agent to testnet (`testnet.summoner.org:8888`) | `opened` (ignored while `locked`) |
|                             | `/go_home`            | Return the other agent to its `default_host:default_port`         | `opened` (ignored while `locked`) |
|                             | `/quit`               | Terminate the other agent                                         | `opened` (ignored while `locked`) |
|                             | `/lock`               | Transition the other agent to `locked`                            | `opened`                          |
|                             | `/open HelloSummoner` | Transition the other agent to `opened`                            | `locked`                          |
| **Local (typed, not sent)** | `/self.travel`        | Move **this** client to testnet                                   | —                                 |
|                             | `/self.go_home`       | Return this client to `default_host:default_port`                 | —                                 |
|                             | `/self.quit`          | Quit this client                                                  | —                                 |
|                             | `/self.lock`          | Set this client’s state to `locked`                               | —                                 |
|                             | `/self.open`          | Set this client’s state to `opened`                               | —                                 |

> 📝 **Note:**
> Remote commands are **executed by the receiver** only when its automaton is in the state shown above. While `locked`, the receiver **prints** most commands (e.g., `/travel`, `/quit`) without executing them; only `/open HelloSummoner` causes an unlock. Local `/self.*` commands act immediately and never send a payload.

</details>

## SDK Features Used

| Feature                                      | Description                                                                      |
| -------------------------------------------- | -------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                   | Instantiates and manages the agent context                                       |
| `client.flow().activate()` / `.ready()`      | Turns on the automaton and prepares the route parser                             |
| `client_flow.add_arrow_style(...)`           | Declares how routes like `opened --> locked` are parsed                          |
| `client_flow.triggers()`                     | Loads trigger names (e.g., `ok`) used in `Move(Trigger.ok)` / `Stay(Trigger.ok)` |
| `@client.upload_states()`                    | Uploads the current state so the flow engine can choose the active receiver      |
| `@client.download_states()`                  | Integrates aggregated next states back into the client’s local `state`           |
| `@client.receive(route="opened --> locked")` | Executes travel/quit and transitions to `locked` on `/lock`                      |
| `@client.receive(route="locked --> opened")` | Prints only; transitions to `opened` on `/open HelloSummoner`                    |
| `Move` / `Stay` (return type `Event`)        | Drive the automaton by signaling a transition or sticking with the current state |
| `@client.send(route="any_state")`            | Reads input, handles `/self.*` commands, sends payloads                          |
| `client.travel_to(...)`, `client.quit()`     | Performs remote moves and clean termination                                      |

> [!NOTE]
> **About `Trigger`:** `client_flow.triggers()` builds a lightweight namespace from a plain-text file named `TRIGGERS`. Each non-empty, non-comment line in that file defines a trigger symbol. For example, if the file contains `ok`, you can reference it as `Trigger.ok` in `Move(Trigger.ok)` / `Stay(Trigger.ok)`. To add more triggers (e.g., `error`, `ignore`), list them on separate lines in `TRIGGERS`, then use them the same way in your handlers.


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use `--config configs/server_config_nojsonlogs.json` for cleaner terminal output.

Then, run two `ChatAgent_3` instances so you can observe state transitions across agents:

```bash
# Terminal 2 (multiline)
python agents/agent_ChatAgent_3/agent.py --multiline 1

# Terminal 3 (single-line)
python agents/agent_ChatAgent_3/agent.py
```


## Simulation Scenarios

This scenario walks through **remote travel**, **remote lock**, **failed unlock**, **successful unlock**, additional **travel**, and finally **remote and local quit** — all while the automaton transitions between `opened` and `locked`.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ChatAgent_3, multiline controller)
python agents/agent_ChatAgent_3/agent.py --multiline 1

# Terminal 3 (ChatAgent_3, single-line target)
python agents/agent_ChatAgent_3/agent.py
```

**Step 1. Say hello locally, then move both agents to testnet.**
You begin on `localhost:8888`. The controller greets; the target receives it. Then you send `/travel` (remote) so the target moves to **testnet**, and `/self.travel` so the controller **follows**.

**Terminal 2 (controller)**

```
[opened]> Hello
~ How are you?
[opened]> /travel
[opened]> /self.travel
... Connected to server @(host=testnet.summoner.org, port=8888)
```

**Terminal 3 (target)**

```
[Received] Hello
How are you?
... Client about to disconnect...
... Connected to server @(host=testnet.summoner.org, port=8888)
```

**Step 2. Lock the target remotely and observe that commands are ignored while locked.**
From testnet, the controller announces the intent, then sends `/lock`. While **locked**, the target prints incoming commands **without executing them** (e.g., `/travel`, `/quit`).

**Terminal 2 (controller)**

```
[opened]> I will lock you
[opened]> /lock
[opened]> /travel
[opened]> /quit
```

**Terminal 3 (target)**

```
[Received] I will lock you
[Received] /lock
[Received] /travel
[Received] /quit
```

**Step 3. Try to unlock with a wrong command (no password), then unlock correctly.**
First you try `/open` (no password) and send a normal message ("Whoops"). The target remains locked and only prints them. Then you send `/open HelloSummoner`—the target prints `[opened]>` and transitions back to **opened**.

**Terminal 2 (controller)**

```
[opened]> /open 
[opened]> Whoops
[opened]> /open HelloSummoner
```

**Terminal 3 (target)**

```
[Received] /open
[Received] Whoops
[Received] /open HelloSummoner
[opened]>    # prompt switches immediately upon unlock
```

**Step 4. Travel again to confirm we’re truly opened, then finish by quitting.**
Now that the target is opened again, remote travel works; you move the target to testnet, follow it, then remote-quit the target and local-quit the controller.

**Terminal 2 (controller)**

```
[opened]> /travel
[opened]> /self.travel
... Connected to server @(host=testnet.summoner.org, port=8888)
[opened]> /quit
[opened]> /self.quit
... Client exited cleanly.
```

**Terminal 3 (target)**

```
... Client about to disconnect...
... Connected to server @(host=testnet.summoner.org, port=8888)
... Client about to disconnect...
... Client exited cleanly.
```
