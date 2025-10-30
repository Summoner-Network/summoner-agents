# `OrchBridgeAgent`

*Refactoring of [LangChainCrewAIAgent](../agent_LangChainCrewAIAgent/) and originally adapted from [GPTRespondAgent](../agent_GPTRespondAgent/).*

A guarded GPT responder that composes a prompt from a **personality** and a **format directive**, then returns answers as JSON. It demonstrates how to subclass `SummonerClient`, use receive/send hooks with a buffer, integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)), and load prompts from [`gpt_config.json`](./gpt_config.json). The agent also uses an identity tag from [`id.json`](./id.json). This agent is designed to interoperate (and works best) with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)). 

This refactor introduces a **small bridge module** that abstracts the model call and framework choice:

* **[`framework_bridge.py`](./framework_bridge.py)** encapsulates the framework logic

  * **JSON mode** goes through a minimal **CrewAI** pipeline (one agent, one task) backed by **LangChain** `ChatOpenAI` configured with `response_format={"type":"json_object"}`.
  * **Text** and **Structured** modes call **LangChain** directly.
* **[`agent_OrchBridgeAgent.py`](./agent.py)** keeps Summoner I/O, identity, and **token/cost guards** and simply delegates to the bridge.

> [!NOTE]
> **Purpose of the refactoring.** `OrchBridgeAgent` is a small reorganization of `LangChainCrewAIAgent` to make **framework composability** explicit. The Summoner agent owns networking, identity, and guardrails. The bridge owns CrewAI and LangChain details. This separation lets developers swap or extend orchestration frameworks in [`framework_bridge.py`](./framework_bridge.py) without changing the Summoner-facing surface.

> [!IMPORTANT]
> **OpenAI credentials required.** The agent calls `load_dotenv()` and expects an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it is available at runtime:
>
> * **.env:**
> ```OPENAI_API_KEY=sk-...your_key...```
>
> * **macOS/Linux terminal:**
> ```export OPENAI_API_KEY="sk-...your_key..."```
>
> * **Windows (PowerShell) terminal:**
> ```$env:OPENAI_API_KEY="sk-...your_key..."```
>
> If the key is missing, the agent will raise: `RuntimeError("OPENAI_API_KEY missing in environment.")`.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),
   * **GPT config** from `gpt_config.json` (or `--gpt <path>`), including:

     * `model`, `output_parsing`, `max_chat_input_tokens`, `max_chat_output_tokens`,
     * `personality_prompt`, `format_prompt`,
     * `sleep_seconds`, `cost_limit_usd`, `debug`,
   * An identity UUID (`my_id`) from `id.json` (or `--id <path>`).
   * Instantiates **`FrameworkBridge`** with `model` and `max_output_tokens`.

3. Incoming messages invoke the receive hook (`@client.hook(Direction.RECEIVE)`):

   * If it is a string starting with `"Warning:"`, logs a warning and drops it.
   * If it is not a dict with `"remote_addr"` and `"content"`, logs:

     ```
     [hook:recv] missing address/content
     ```

     and drops it.
   * Otherwise, logs:

     ```
     [hook:recv] <addr> passed validation
     ```

     and forwards the message to the receive handler.

4. The receive handler (`@client.receive(route="")`) enqueues `content` into `message_buffer` and logs:

   ```
   Buffered message from:(SocketAddress=<addr>).
   ```

5. Before sending, the send hook (`@client.hook(Direction.SEND)`) logs:

   ```
   [hook:send] sign <uuid>
   ```

   It wraps raw strings into `{"message": ...}`, adds `{"from": my_id}`, and forwards the message.

6. The send handler (`@client.send(route="")`) dequeues the payload and builds a **single user message**:

   ```
   <personality_prompt>
   <format_prompt>

   Content:
   <JSON-serialized payload>
   ```

   Then it calls the model using **token and cost guardrails**:

   * Computes prompt token count and estimated cost using `safeguards`.
   * Aborts if tokens exceed `max_chat_input_tokens` or the estimated cost exceeds `cost_limit_usd`.
   * Delegates to the bridge:

     * `"json"` → `FrameworkBridge.run_json(...)` which executes a minimal **CrewAI** task on top of **LangChain** `ChatOpenAI` with strict JSON.
     * `"text"` → `FrameworkBridge.run_text(...)` which calls **LangChain** directly.
     * `"structured"` → `FrameworkBridge.run_structured(..., output_type=YourModel)` which returns a schema-validated object.
   * Normalizes the final output to:

     ```json
     {"answers": { ... }}
     ```

     (If the model did not return a dict, it falls back to an empty object.)

   Logs a summary:

   ```
   [respond] model=<model> id=<uuid> cost=<usd_or_none>
   ```

7. Sleeps for `sleep_seconds` and continues until stopped.

</details>

## SDK Features Used

| Feature                               | Description                                                             |
| ------------------------------------- | ----------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`       | Subclasses `SummonerClient` to load configs, identity, and manage state |
| `@client.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling               |
| `@client.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID              |
| `@client.receive(route=...)`          | Buffers validated messages into the queue                               |
| `@client.send(route=...)`             | Builds the GPT prompt, enforces guards, and returns normalized answers  |
| `client.logger`                       | Logs hook activity, buffering, and send lifecycle events                |
| `client.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue              |
| `client.run(...)`                     | Connects to the server and starts the asyncio event loop                |
| **FrameworkBridge**                   | Encapsulates **CrewAI** (JSON via one-task Crew using **LangChain** LLM) and **LangChain** (text and structured) |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json`. A typical `gpt_config.json` looks like:

```json
{
  "model": "gpt-4o-mini",
  "sleep_seconds": 0.5,
  "output_parsing": "json",
  "cost_limit_usd": 0.004,
  "debug": true,
  "max_chat_input_tokens": 4000,
  "max_chat_output_tokens": 1500,
  "personality_prompt": "You are a helpful, concise assistant. [...]",
  "format_prompt": "You will receive ONE input block labeled \"Content:\" that may take various forms [...]"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_OrchBridgeAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_OrchBridgeAgent/agent_OrchBridgeAgent.py
```

Optional CLI flags:

* `--gpt <path>` Use a custom `gpt_config.json` path.
* `--id <path>` Use a custom `id.json` path.
* `--config <path>` Summoner client config path (defaults to `configs/client_config.json`).

## Simulation Scenarios

These scenarios show how `OrchBridgeAgent` consumes structured input from `InputAgent` and replies with normalized `{"answers": ...}`. The JSON mode goes through **CrewAI** with a **LangChain** backend. Text and structured modes call **LangChain** directly.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: OrchBridgeAgent
python agents/agent_OrchBridgeAgent/agent_OrchBridgeAgent.py
```

**Scenario A — Single instruction (no qid):**

In Terminal 2 (InputAgent), type:

```
> {"instruction":"List two advantages of unit tests."}
```

Terminal 3 (`OrchBridgeAgent`) logs token/cost diagnostics (when `debug: true`) and returns:

```
[respond] model=gpt-4o-mini id=6fb3fedd-ebca-43b8-b915-fd25a6ecf78a cost=9.57e-05
```

Then, Terminal 2 receives:

```
[Received] {'answers': {'default': '1. Early bug detection: Unit tests help identify issues in code at an early stage, making it easier to fix before they escalate. 2. Simplified code changes: With a suite of unit tests, developers can confidently make modifications, knowing that existing functionality is verified.'}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
> 
```

**Scenario B — Multiple questions without qids:**

In Terminal 2, type:

```
> {"questions":["Name one pitfall of global state.","Name one benefit of code review."]}
```

Terminal 2 receives (keys indexed as `"0"`, `"1"`):

```
[Received] {'answers': {'0': 'One pitfall of global state is that it can lead to unpredictable behavior and bugs due to shared mutable data affecting different parts of a program.', '1': 'One benefit of code review is that it improves code quality by allowing multiple developers to catch errors, share knowledge, and ensure adherence to coding standards.'}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
> 
```

**Scenario C — Questions with explicit qids:**

You can use [`InputAgent`](../agent_InputAgent/)'s multi-line mode (`\` + Enter) to compose JSON across lines. Type in Terminal 2:

```
> {"items":[
~ {"qid":"Q17","question":"Preferred logging level for local dev?"},
~ {"qid":"Q18","question":"One mitigation for flaky tests?"}
~ ]}
```

Terminal 2 receives:

```
[Received] {'answers': {'Q17': "The preferred logging level for local development is typically 'DEBUG' to capture detailed information during development.", 'Q18': 'One mitigation for flaky tests is to implement retries, where tests are automatically re-run a set number of times before failing.'}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
> 
```

**Scenario D — Cost/Token guard kicks in (illustrative):**

If the composed prompt exceeds `max_chat_input_tokens` (see [`gpt_config.json`](./gpt_config.json)), `OrchBridgeAgent` prints:

```
Prompt tokens: 4501 > 4000? True
[chat] Estimated cost (for 1500 output tokens): $0.003750
Tokens exceeded — unable to send the request.
```

and returns:

```
[Received] {'answers': {}}
```