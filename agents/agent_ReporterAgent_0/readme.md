# `ReportAgent_0`

A buffered reporter that aggregates incoming messages for a short window and emits a **single joined payload**. It demonstrates a receive-buffer → consolidated-send pattern using an internal `asyncio.Queue`.

> [!NOTE]
> Compare with [`ReportAgent_1`](../agent_ReportAgent_1/) to see the same logic implemented with **`multi=True`**, which emits a batch of messages without joining them.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, `setup()` creates an internal `asyncio.Queue` named `message_buffer`.
2. The receive handler (`@client.receive(route="")`):

   * Extracts `content` from a dict payload if present, otherwise treats the inbound object as the message string.
   * Enqueues the string into `message_buffer`.
   * Prints `\r[From server]` if the text starts with `"Warning:"`, else `\r[Received]`, followed by the message.
3. The send handler (`@client.send(route="")`):

   * Waits for the **first** message (blocking).
   * Sleeps for **5 seconds** to allow additional messages to arrive.
   * Drains any remaining messages from the queue non-blockingly.
   * Returns a **single string** formed by joining the collected messages with newline characters.
4. Steps 2–3 repeat until the client is stopped (e.g., Ctrl+C).

> 📝 **Note:**
> **Idle until first message.** The `@client.send` coroutine blocks until at least one message is buffered. If no messages arrive, nothing is sent and the agent remains idle.

> 💡 **Tip:**
> **Adjustable batching window.** The 5-second wait after the first buffered message defines the collection window. Tweak `asyncio.sleep(5)` in `custom_send()` to change the window or replace it with another flush policy (for example, send after N messages).


</details>

## SDK Features Used

| Feature                                 | Description                                              |
| --------------------------------------- | -------------------------------------------------------- |
| `SummonerClient(name=...)`              | Instantiates and manages the agent                       |
| `@client.receive(route="")`             | Buffers inbound messages into an internal queue          |
| `@client.send(route="")`                | Consolidates buffered messages into one payload          |
| `client.loop.run_until_complete(setup)` | Initializes the queue before starting the client         |
| `client.run(host, port, config_path)`   | Connects to the server and starts the asyncio event loop |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then, run the report agent:

```bash
python agents/agent_ReportAgent_0/agent.py
```

## Simulation Scenarios

### Scenario 1: One chat sender, one report consolidator

This scenario shows how `ReportAgent_0` buffers several messages from a chat client and returns a single, newline-joined report.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ReportAgent_0)
python agents/agent_ReportAgent_0/agent.py

# Terminal 3 (ChatAgent_0)
python agents/agent_ChatAgent_0/agent.py
```

**Terminal 3 (ChatAgent_0)**
Start the chat client and type three lines quickly, pressing Enter after each one. Then stop typing for a moment so the reporter can collect and send the batch.

```text
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-25 08:39:54.072 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello
> How are you?
> Bye
```

**Terminal 2 (ReportAgent_0)**
Keep this terminal visible. As you type in Terminal 3, watch the reporter print and buffer each line immediately.

```text
python agents/agent_ReportAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-25 08:39:44.796 - ReportAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello
[Received] How are you?
[Received] Bye
```

**Terminal 3 (ChatAgent_0)**
After roughly 5 seconds from the **first** line you typed, the reporter emits one joined message. You will receive the consolidated report here as a single payload.

```text
[Received] Hello
How are you?
Bye
```

This confirms the buffered-then-join behavior.

### Scenario 2: Two chat senders, one report consolidator

Here two `ChatAgent_0` instances speak at the same time. `ReportAgent_0` buffers both senders and returns one joined report.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ReportAgent_0)
python agents/agent_ReportAgent_0/agent.py

# Terminal 3 (ChatAgent_0)  # "bob"
python agents/agent_ChatAgent_0/agent.py

# Terminal 4 (ChatAgent_0)  # "Alice"
python agents/agent_ChatAgent_0/agent.py
```

**Terminal 3 (ChatAgent_0, bob)**
Start the chat client for bob. Send your greeting first, then keep the window open to observe messages arriving from Alice and the later consolidated report.

```text
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-25 08:46:51.994 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello it's bob!
[Received] Hello it's Alice!
[Received] Hello it's bob!
Hello it's Alice!
>
```

**Terminal 4 (ChatAgent_0, Alice)**
Start the second chat client for Alice. Wait to see bob’s greeting arrive, then send your own. Leave the terminal open so you can see the consolidated report later.

```text
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-25 08:46:52.675 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello it's bob!
> Hello it's Alice!
[Received] Hello it's bob!
Hello it's Alice!
>
```

**Terminal 2 (ReportAgent_0)**
Keep the reporter visible. As both chat clients speak, the reporter prints and buffers each line. Do not type here; just observe the buffering.

```text
python agents/agent_ReportAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-08-25 08:46:50.786 - ReportAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello it's bob!
[Received] Hello it's Alice!
```

After the 5 second collection window that starts with the first received line, the reporter sends one joined payload containing both messages. Each chat client will then receive the single consolidated report, confirming that `ReportAgent_0` aggregates across multiple sources before emitting.