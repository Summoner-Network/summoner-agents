# `StreamAgent_1`

This agent is a **client-side streaming example** built with the Summoner SDK. It is derived from [`EchoAgent_0`](../agent_EchoAgent_0) and reuses the same receive-then-buffer-then-send pattern, but replaces the static echo payload with an LLM stream. The goal is to show how to trigger a streaming LLM response on `@receive`, buffer streamed tokens in an `asyncio.Queue`, and emit those tokens back to the server via `@send`.

The main difference from `StreamAgent_0` is the **send strategy**: `StreamAgent_1` uses a **busy/polling send loop** (short sleep + `get_nowait`) rather than waiting on the queue with a timeout. This makes the send cadence easy to tune for rate-limiting experiments, at the cost of more frequent wakeups when the queue is empty.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. The agent connects to the server.
2. When it receives a message of the form:

   ```json
   {"remote_addr": "...", "content": ...}
   ```

   it interprets `content` as a prompt (string or dict) and starts an LLM stream.
3. While the LLM is streaming, the agent pushes events into a queue:

   * `{"type": "stream_start", "stream_id": ...}`
   * `{"type": "token", "stream_id": ..., "token": ...}`
   * `{"type": "stream_end", "stream_id": ...}`
   * (optional) `stream_cancelled` if a new prompt arrives while the previous stream is active
   * `stream_error` if the stream fails
4. The `@send` route runs in a short-loop mode:

   * it sleeps briefly (`0.01s`)
   * it tries to pop one queued event (`get_nowait`)
   * if the queue is empty, it returns nothing

> 📝 **Note:**
> 
> * Only one stream is active at a time. A new received prompt cancels the previous stream task.
> * The server is responsible for attaching `remote_addr` and packaging the outbound payload into `content`.
> * The send loop in this agent is intentionally **polling-based**. It trades efficiency for a controllable cadence.

</details>

## SDK Features Used

| Feature                      | Description                                                              |
| ---------------------------- | ------------------------------------------------------------------------ |
| `SummonerClient(name=...)`   | Creates and manages the agent instance                                   |
| `@client.receive(route=...)` | Registers an async handler triggered on incoming server messages         |
| `@client.send(route=...)`    | Registers an async sender that periodically emits payloads to the server |
| `client.run(...)`            | Connects the client to the server and initiates the async lifecycle      |

## How to Run

First, ensure the Summoner server is running:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Set your OpenAI credentials (recommended via `.env` in the agent folder):

```bash
export OPENAI_API_KEY="..."
```

Then run the agent:

```bash
python agents/agent_StreamAgent_1/agent.py
```

If you want to point to a specific client config:

```bash
python agents/agent_StreamAgent_1/agent.py --config configs/client_config.json
```

## Simulation Scenarios

This scenario demonstrates an end-to-end streaming round-trip across three processes:

* **Server**: routes messages between clients and provides the envelope `{remote_addr, content}`.
* **`StreamAgent_1`**: receives a prompt, starts a streaming LLM call, and emits a sequence of streaming events (`stream_start`, `token`, `stream_end`).
* **`InputAgent`**: provides an interactive CLI, sending prompts and printing responses as they arrive.

### 1) Start the three terminals

```sh
# Terminal 1: start the server
python server.py

# Terminal 2: start the streaming agent
python agents/agent_StreamAgent_1/agent.py

# Terminal 3: start the interactive input agent
python agents/agent_InputAgent/agent.py
```

### 2) Enter a prompt in the InputAgent

In Terminal 3 you should see the InputAgent connect, then a prompt:

```log
python agents/agent_InputAgent/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2026-01-30 18:48:39.168 - InputAgent - INFO - Connected to server @(host=127.0.0.1, port=8888)
> How are you?
```

When you type `How are you?` and press Enter:

1. **`InputAgent`** sends the prompt to the server.
2. The **server forwards** it to **`StreamAgent_1`**, packaging it as:

   ```json
   {"remote_addr": "...", "content": "How are you?"}
   ```
3. **`StreamAgent_1`** begins streaming an LLM response. As tokens arrive, it pushes events into its internal queue.
4. The agent's `@send` loop repeatedly wakes up (every `0.01s`), pops queued events when available, and emits them back to the server.
5. The **server forwards** those events to **`InputAgent`**, which prints them immediately.

### 3) Observe the streamed events in `InputAgent`

In Terminal 3, you should see a streaming envelope followed by many token events:

```log
[Received] {'type': 'stream_start', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f'}
[Received] {'type': 'token', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f', 'token': "I'm"}
[Received] {'type': 'token', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f', 'token': ' just'}
[Received] {'type': 'token', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f', 'token': ' a'}
...
[Received] {'type': 'token', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f', 'token': '?'}
[Received] {'type': 'stream_end', 'stream_id': '6e475111-8633-42ea-a456-f146366f131f'}
>
```

What to pay attention to:

* **`stream_id`**: all events for a single streamed response share the same `stream_id`. This is what allows the receiver (InputAgent or another client) to group tokens into the right response, even if multiple streams exist in the system.
* **Token granularity**: tokens arrive as small chunks (sometimes including leading spaces). This is normal for streamed generation.
* **Ordering**: you should always see `stream_start` first and `stream_end` last for a given `stream_id`, with one or many `token` events in between.

### 4) Observe `StreamAgent_1`'s logs

In Terminal 2, `StreamAgent_1` logs when it receives a prompt and starts an LLM stream:

```log
python agents/agent_StreamAgent_1/agent.py
[DEBUG] Loaded config from: configs/client_config.json
2026-01-30 18:42:17.003 - StreamAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
2026-01-30 18:48:42.192 - StreamAgent_1 - INFO - Triggering LLM streaming for remote_addr=127.0.0.1:50490 prompt='How are you?'
```

> [!TIP]
> 
> `StreamAgent_1` is useful when you want to experiment with pacing:
>
> * you can change `await asyncio.sleep(0.01)` to `0.05`, `0.1`, `0.5`, etc.
> * the maximum event emission rate becomes easy to reason about
> * the cost is that, when idle, the loop still wakes up periodically
>
> If you want a more efficient version (idle-friendly), use the `StreamAgent_0` approach where `@send` waits on the queue with a timeout rather than polling.
