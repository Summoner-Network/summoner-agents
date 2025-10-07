# `ReporterAgent_2`

A **frame-based** reporter that aggregates incoming messages on a per-frame basis. It runs at a fixed FPS (Frames Per Second), collecting all messages received during a frame and emitting them in a **single JSON payload**. This demonstrates a continuous receive-buffer → frame-based-send pattern using an internal `asyncio.Queue`.

> [\!NOTE]
> This agent sends data on every frame, even if no messages are received. This is useful for systems requiring a constant "heartbeat" or state update.

> [\!NOTE]
> This agent targets 60 FPS while there are no messages being sent, but emits frames as frequently as possible when there are messages being sent.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1.  On startup, `setup()` creates an internal `asyncio.Queue` named `message_buffer`.
2.  The receive handler (`@client.receive(route="")`):
      * Extracts `content` from a dict payload if present, otherwise treats the inbound object as the message string.
      * Enqueues the string into `message_buffer`.
      * Prints `\r[Received]` followed by the message.
3.  The send handler (`@client.send(route="")`):
      * Operates on a continuous loop, targeting **60 FPS**.
      * On each frame, it non-blockingly drains all messages that have accumulated in the queue since the last frame.
      * It constructs a dictionary containing:
          * `frameNumber`: An integer counter for the current frame.
          * `deltaEvents`: A list of the string messages collected during the frame. This list is empty if no messages were received.
          * `deltaTiming`: The elapsed time for the frame in nanoseconds.
      * It returns this dictionary as a **JSON string**.
      * It then sleeps for the remainder of the frame's time slice (e.g., \~16.67ms for 60 FPS) before starting the next frame.
4.  Step 3 repeats at the target FPS until the client is stopped (e.g., Ctrl+C).

> 💡 **Tip:**
> **Adjustable Frame Rate.** The agent's update rate is controlled by the `FPS` global variable. Change `FPS = 60` in the script to make it send updates more or less frequently.

</details>

## SDK Features Used

| Feature | Description |
| :--- | :--- |
| `SummonerClient(name=...)` | Instantiates and manages the agent |
| `@client.receive(route="")` | Buffers inbound messages into an internal queue |
| `@client.send(route="")` | Drains the queue and sends a consolidated JSON payload each frame |
| `client.loop.run_until_complete(setup)` | Initializes the queue before starting the client |
| `client.run(host, port, config_path)` | Connects to the server and starts the asyncio event loop |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [\!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Then, run the report agent:

```bash
python agents/agent_ReportAgent_0/agent.py
```

## Simulation Scenarios

### Scenario 1: One chat sender, one frame-based reporter

This scenario shows how `ReportAgent_0` batches messages from a chat client into frame-based JSON payloads.

```bash
# Terminal 1 (server)
python server.py

# Terminal 2 (ReportAgent_0)
python agents/agent_ReportAgent_0/agent.py

# Terminal 3 (ChatAgent_0)
python agents/agent_ChatAgent_0/agent.py
```

**Terminal 3 (ChatAgent\_0)**
Start the chat client. Type the first two lines quickly (within \~16ms), press Enter after each, then wait a second before typing the third line.

```text
python agents/agent_ChatAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-10-07 17:03:15.123 - ChatAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Hello there
> General Kenobi
> You are a bold one.
```

**Terminal 2 (ReportAgent\_0)**
This terminal shows the agent receiving and buffering the messages in real-time.

```text
python agents/agent_ReportAgent_0/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2025-10-07 17:03:15.088 - ReportAgent_0 - INFO - Connected to server @(host=127.0.0.1, port=8888)
[Received] Hello there
[Received] General Kenobi
[Received] You are a bold one.
```

**Terminal 3 (ChatAgent\_0)**
The chat client will receive multiple JSON payloads from the reporter. Because the first two messages were sent quickly, they are captured in the **same frame** and appear in the same `deltaEvents` list. The third message, sent later, arrives in a subsequent frame's payload.

```text
[Received] {"frameNumber": 75, "deltaEvents": ["Hello there", "General Kenobi"], "deltaTiming": 16679234}
[Received] {"frameNumber": 138, "deltaEvents": ["You are a bold one."], "deltaTiming": 16701987}
```

*(Note: `frameNumber` and `deltaTiming` values will vary with each run.)*

### Scenario 2: Two chat senders, one frame-based reporter

Here, two `ChatAgent_0` instances send messages that are batched by `ReportAgent_0` based on the frame they were received in.

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

**Actions**

1.  In **Terminal 3**, Bob types `Hi Alice!` and presses Enter.
2.  In **Terminal 4**, Alice immediately types `Hi Bob!` and presses Enter.

**Expected Output (in both Terminal 3 and 4)**
If the messages from Bob and Alice arrive at the server close enough together to be processed by `ReportAgent_0` in the same frame, both chat clients will receive a single JSON payload containing both messages.

```text
[Received] {"frameNumber": 210, "deltaEvents": ["Hi Alice!", "Hi Bob!"], "deltaTiming": 16694321}
```

If the messages arrive in different frames, each chat client will receive two separate JSON payloads, one for each message, similar to the output in Scenario 1. This demonstrates how the agent consolidates events based on timing.