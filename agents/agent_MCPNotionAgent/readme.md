# `MCPNotionAgent`

A guarded GPT-powered agent that decides whether to call a **Notion tool exposed via an MCP server** and, when appropriate, returns a structured response for a **search**, a **database query**, or a **page/block children listing**. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an MCP tool call (`notion_handle_request`). If no Notion call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external tool and which operation to run,
* call an MCP tool over Streamable HTTP and return normalized results for several actions.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/). Compared to `GPTNotionAgent`, this version moves the Notion REST logic into an MCP server and replaces direct `aiohttp` usage in the agent with an MCP tool call.

> [!IMPORTANT]
> **OpenAI credentials required.** The agent calls `load_dotenv()` and expects an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it is available at runtime:
>
> * **.env:**
>   `OPENAI_API_KEY=sk-...your_key...`
>
> * **macOS/Linux terminal:**
>   `export OPENAI_API_KEY="sk-...your_key..."`
>
> * **Windows (PowerShell) terminal:**
>   `$env:OPENAI_API_KEY="sk-...your_key..."`
>
> If the key is missing, the agent will raise: `RuntimeError("OPENAI_API_KEY missing in environment.")`.

> [!NOTE]
> **Notion token required to access your workspace.**
> The MCP server calls `load_dotenv()` and reads `NOTION_API_KEY` (or `NOTION_TOKEN`) from the environment. To let `MCPNotionAgent` talk to your Notion workspace, you must create an internal integration and share the relevant pages/databases with it:
>
> 1. Go to your Notion integrations page: [https://www.notion.so/profile/integrations/](https://www.notion.so/profile/integrations/).
> 2. Create a new **internal integration** and copy the **secret token**.
> 3. In Notion, share the pages and databases you want the agent to access with this integration.
> 4. Store the token in your `.env`:
>
>    ```env
>    NOTION_API_KEY=secret_xxx_from_notion
>    ```
>
>    (The server also accepts `NOTION_TOKEN` as a fallback name.)
>
> Ensure your `.env` is in `.gitignore`. Without a token, the MCP tool will return a `missing_notion_token` error when invoked.

> [!IMPORTANT]
> **MCP server required.** This agent expects an MCP server exposing the tool:
>
> * `notion_handle_request(action, query?, database_id?, block_id?, page_size?, filter_object?) -> dict`
>
> By default, the MCP server is assumed to run at `http://localhost:8000/mcp` (configurable).

---

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
     * `mcp_url` (URL of the MCP server endpoint),
   * an identity UUID (`my_id`) from `id.json` (or `--id <path>`).

   The Notion token is not read by the agent. It is read by the MCP server.

3. Incoming messages invoke the receive-hook (`@agent.hook(Direction.RECEIVE)`):

   * If it is a string starting with `"Warning:"`, logs a warning and drops it.
   * If it is not a dict with `"remote_addr"` and `"content"`, logs:

     ```text
     [hook:recv] missing address/content
     ```

     and drops it.
   * Otherwise, logs:

     ```text
     [hook:recv] <addr> passed validation
     ```

     and forwards the message to the receive handler.

4. The receive handler (`@agent.receive(route="")`) enqueues `msg["content"]` into `message_buffer` and logs:

   ```text
   Buffered message from:(SocketAddress=<addr>).
   ```

5. Before sending, the send-hook (`@agent.hook(Direction.SEND)`) logs:

   ```text
   [hook:send] sign <uuid>
   ```

   It wraps raw strings into `{"message": ...}`, adds `{"from": my_id}`, and forwards the message to the send handler.

6. The send handler (`@agent.send(route="")`) dequeues the payload (`content`) and builds a **single user message**:

   ```text
   <personality_prompt>
   <format_prompt>

   Content:
   <JSON-serialized payload>
   ```

   It then:

   1. Calls `gpt_call_async(...)` with `output_parsing="json"`.
   2. Interprets the GPT output as a **tool argument dictionary** `tool_args`:

      * If GPT returns a string, it tries to `json.loads` it.
      * If the result is not a dict, it falls back to `{}`.

7. The GPT output is expected to be either:

   * an **empty object** `{}` meaning *do not call Notion*, or
   * a dict with fields describing a Notion operation, for example:

     ```json
     {"action": "block_children", "block_id": "272996a6-195e-80fa-ac16-e9ca2209b07d", "page_size": 50}
     ```

     or

     ```json
     {"action": "search", "query": "External tasks", "page_size": 5, "filter_object": "page"}
     ```

   The send handler then:

   * checks if `tool_args` is non-empty and contains a non-empty `"action"` string,

   * if yes, calls the MCP tool:

     ```python
     mcp_result = await mcp_call_tool(
         mcp_url=agent.mcp_url,
         tool_name="notion_handle_request",
         arguments=tool_args,
     )
     api_result = unwrap_mcp_result(mcp_result)
     performed_call = True
     ```

   * if no, it sets:

     ```python
     api_result = {
         "error": "no_notion_call_requested_or_missing_action",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "notion",
     "performed_call": true,
     "result": {
       "action": "block_children",
       "block_id": "272996a6-195e-80fa-ac16-e9ca2209b07d",
       "page_size": 50,
       "status": 200,
       "data": {
         "object": "list",
         "results": [
           { "object": "block", "id": "...", "type": "heading_1", "heading_1": { "rich_text": [ ... ] } },
           { "object": "block", "id": "...", "type": "paragraph", "paragraph": { "rich_text": [ ... ] } }
         ],
         "has_more": false,
         "next_cursor": null
       },
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "action": "block_children",
       "block_id": "272996a6-195e-80fa-ac16-e9ca2209b07d",
       "page_size": 50
     },
     "to": "<uuid of sender>"
   }
   ```

   If the Notion token is missing or an identifier is incomplete, `result` can contain an error payload such as:

   ```json
   {
     "error": "missing_notion_token",
     "details": "Set NOTION_API_KEY or NOTION_TOKEN in the environment.",
     "tool_args": { ... }
   }
   ```

   The agent logs a summary:

   ```text
   [respond] model=<model> id=<uuid> cost=<usd_or_none> performed_call=<True|False>
   ```

9. Sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>

---

## SDK Features Used

| Feature                              | Description                                                                                         |
| ------------------------------------ | --------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                             |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                           |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                          |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                           |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls MCP tool, returns result |
| `agent.logger`                       | Logs hook activity, buffering, MCP call lifecycle, and send events                                  |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                          |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                            |

---

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Second, start the MCP server for this agent (Notion tool provider):

```bash
python agents/agent_MCPNotionAgent/mcp_server.py
```

This starts the Streamable HTTP MCP endpoint at:

```text
http://localhost:8000/mcp
```

Prepare `gpt_config.json` and `id.json` in `agents/agent_MCPNotionAgent/`.

A **typical `gpt_config.json` for MCPNotionAgent** (only the important parts):

```json
{
  "model": "gpt-4o-mini",
  "sleep_seconds": 0.5,
  "output_parsing": "json",
  "cost_limit_usd": 0.004,
  "debug": true,
  "max_chat_input_tokens": 4000,
  "max_chat_output_tokens": 1500,

  "mcp_url": "http://localhost:8000/mcp",

  "personality_prompt": "You are a helpful, concise assistant. Tone: neutral and objective. How you operate: answer directly and completely; prefer clarity over verbosity; avoid speculation and state assumptions briefly only when unavoidable; keep outputs deterministic and free of meta-commentary.",

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"action_hint\", \"database_id\", \"block_id\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking to search or inspect content in Notion, or would clearly benefit from such an operation. Typical cues include phrases like \"search in Notion\", \"find this page\", \"query this database\", \"list blocks under\", or a raw Notion page/block ID.\n2) If a Notion call IS appropriate and you can identify the necessary parameters, OUTPUT a JSON object with the following keys:\n   - \"action\": one of \"search\", \"database_query\", or \"block_children\".\n   - For \"search\": include \"query\" (string). Optionally include \"page_size\" (1..100) and \"filter_object\" (\"page\" or \"database\") if clearly requested.\n   - For \"database_query\": include \"database_id\" (string). Optionally include \"page_size\" (1..100).\n   - For \"block_children\": include \"block_id\" (string). Optionally include \"page_size\" (1..100).\n3) If a Notion call is NOT appropriate, or if you cannot reliably infer the required identifiers, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call Notion, you MUST include the key \"action\" and the keys required for that action.\n- Do NOT include any keys other than: \"action\", \"query\", \"database_id\", \"block_id\", \"page_size\", \"filter_object\".\n- Do NOT add explanations or natural-language text outside the JSON.\n\nExamples:\n- \"Search my Notion workspace for pages about external tasks\" → {\"action\": \"search\", \"query\": \"External tasks\"}\n- \"Query the database 897e5a76ae524b489fdfe71f5945d1af and show me up to 20 entries\" → {\"action\": \"database_query\", \"database_id\": \"897e5a76ae524b489fdfe71f5945d1af\", \"page_size\": 20}\n- \"List the blocks under page b55c9c91-384d-452b-81db-d1ef79372b75\" → {\"action\": \"block_children\", \"block_id\": \"b55c9c91-384d-452b-81db-d1ef79372b75\"}\n- \"Explain what Notion is\" → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_MCPNotionAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_MCPNotionAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).

---

## Simulation Scenarios

These scenarios show how `MCPNotionAgent` consumes input from `InputAgent` and either:

* calls the MCP Notion tool when a workspace operation is appropriate, or
* returns a small diagnostic payload when it is not.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: MCP server (Notion tool)
python agents/agent_MCPNotionAgent/mcp_server.py

# Terminal 4: MCPNotionAgent
python agents/agent_MCPNotionAgent/agent.py
```

### Scenario A — InputAgent, inspect a specific page by ID

In Terminal 2 (`InputAgent`), type:

```text
> What can you tell me about the page: 202996a6195e80faac16e9ca2209c07d
```

Expected behavior:

1. GPT produces tool args similar to:

   ```json
   {
     "action": "block_children",
     "block_id": "202996a6-195e-80fa-ac16-e9ca2209c07d",
     "page_size": 50
   }
   ```

2. The agent calls the MCP tool `notion_handle_request` with those arguments.

3. The agent returns a payload whose `result.data.results` contains the Notion blocks.

### Scenario B — InputAgent, search Notion for a topic

In Terminal 2, type:

```text
> Search in my Notion workspace for pages (only) about investors and show me up to 5 results.
```

Expected tool args:

```json
{
  "action": "search",
  "query": "investors",
  "page_size": 5,
  "filter_object": "page"
}
```

Expected response:

* `performed_call` is `True`
* `result.action` is `search`
* `result.data.results` contains pages

### Scenario C — InputAgent, no Notion call requested

In Terminal 2, type:

```text
> {"instruction":"Explain in simple terms what Notion is."}
```

Expected behavior:

* GPT outputs `{}`.
* `performed_call` is `False`.
* `result.error` is `no_notion_call_requested_or_missing_action`.

---

You can use these scenarios to verify:

* that MCP is called when the intent is clearly “search/query/list something inside my Notion workspace”, and
* that no call is made when the request is purely explanatory and does not require workspace access.
