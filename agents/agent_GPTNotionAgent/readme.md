# `GPTNotionAgent`

A guarded GPT powered agent that decides whether to call the **Notion API** and, when appropriate, returns a structured response for a **search**, a **database query**, or a **page/block children listing**. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an async `notion_handle_request` helper. If no Notion call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API and which operation to run,
* call the **Notion REST API** via `aiohttp` and return normalized results for several actions.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an API call layer to the Notion API to fulfill Notion search / query / page inspection requests.

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

> [!NOTE]
> **Notion token required to access your workspace.**
> To let `GPTNotionAgent` talk to your Notion workspace, you must create an internal integration and share the relevant pages/databases with it:
>
> 1. Go to your Notion integrations page: [https://www.notion.so/profile/integrations/](https://www.notion.so/profile/integrations/).
> 2. Create a new **internal integration** and copy the **secret token**.
> 3. In Notion, share the pages and databases you want the agent to access with this integration (as you would share with a user).
> 4. Store the token in your `.env`:
>
>    ```env
>    NOTION_API_KEY=secret_xxx_from_notion
>    ```
>
>    (The agent also accepts `NOTION_TOKEN` as a fallback name.)
>
> Ensure your `.env` is in `.gitignore`. Without a token, the agent will return a `missing_notion_token` error when it tries to call Notion.


## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),
   * a Notion token from `NOTION_API_KEY` or `NOTION_TOKEN` (optional but required for actual calls),
   * **GPT config** from `gpt_config.json` (or `--gpt <path>`), including:

     * `model`, `output_parsing`, `max_chat_input_tokens`, `max_chat_output_tokens`,
     * `personality_prompt`, `format_prompt`,
     * `sleep_seconds`, `cost_limit_usd`, `debug`,
   * An identity UUID (`my_id`) from `id.json` (or `--id <path>`).

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

   This works whether `content` is a raw string or a JSON-like object; in the string case it is serialized as a JSON string.

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
   * if yes, calls:

     ```python
     api_result = await notion_handle_request(tool_args)
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
           { "object": "block", "id": "...", "type": "paragraph", "paragraph": { "rich_text": [ ... ] } },
           ...
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

   If the Notion token is missing or an identifier is incomplete, `result` contains an error payload such as:

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

| Feature                              | Description                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                           |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                         |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                        |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                         |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls Notion, returns result |
| `agent.logger`                       | Logs hook activity, buffering, Notion calls, and send lifecycle events                            |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                        |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                          |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTNotionAgent/`.

A **typical `gpt_config.json` for GPTNotionAgent** (only the important parts):

```json
{
  "model": "gpt-4o-mini",
  "sleep_seconds": 0.5,
  "output_parsing": "json",
  "cost_limit_usd": 0.004,
  "debug": true,
  "max_chat_input_tokens": 4000,
  "max_chat_output_tokens": 1500,

  "personality_prompt": "You are a helpful, concise assistant. Tone: neutral and objective. How you operate: answer directly and completely; prefer clarity over verbosity; avoid speculation and state assumptions briefly only when unavoidable; keep outputs deterministic and free of meta-commentary.",

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"action_hint\", \"database_id\", \"block_id\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking to search or inspect content in Notion, or would clearly benefit from such an operation. Typical cues include phrases like \"search in Notion\", \"find this page\", \"query this database\", \"list blocks under\", or a raw Notion page/block ID.\n2) If a Notion call IS appropriate and you can identify the necessary parameters, construct arguments for the Notion helper function and OUTPUT a JSON object with the following keys:\n   - \"action\": one of \"search\", \"database_query\", or \"block_children\".\n   - For \"search\": include\n       * \"query\": a STRING for the Notion search query.\n       * \"page_size\": an INTEGER between 1 and 100 (optional; if missing, the code will default to 10).\n       * Optionally \"filter_object\": a STRING such as \"page\" or \"database\" if the user clearly wants one type only.\n   - For \"database_query\": include\n       * \"database_id\": a STRING with the Notion database ID.\n       * \"page_size\": an INTEGER between 1 and 100 (optional; if missing, the code will default to 10).\n   - For \"block_children\": include\n       * \"block_id\": a STRING with the Notion page or block ID whose children should be retrieved.\n       * \"page_size\": an INTEGER between 1 and 100 (optional; if missing, the code will default to 10).\n3) If a Notion call is NOT appropriate, or if you cannot reliably infer the required identifiers (e.g., database_id or block_id), OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call Notion, you MUST include the key \"action\" and the keys required for that action (e.g., \"query\" for search, \"database_id\" for database_query, \"block_id\" for block_children).\n- You MAY include \"page_size\" when the user implies a limit (e.g., \"top 5 results\"); otherwise omit it.\n- Do NOT include any keys other than: \"action\", \"query\", \"database_id\", \"block_id\", \"page_size\", \"filter_object\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not call the API; you only prepare the parameters.\n\nExamples:\n- User asks: \"Search my Notion workspace for pages about external tasks\" → {\"action\": \"search\", \"query\": \"External tasks\"}\n- User asks: \"Query the database 897e5a76ae524b489fdfe71f5945d1af and show me up to 20 entries\" → {\"action\": \"database_query\", \"database_id\": \"897e5a76ae524b489fdfe71f5945d1af\", \"page_size\": 20}\n- User asks: \"List the blocks under page b55c9c91-384d-452b-81db-d1ef79372b75\" → {\"action\": \"block_children\", \"block_id\": \"b55c9c91-384d-452b-81db-d1ef79372b75\"}\n- User asks: \"Explain what Notion is\" (no workspace operation requested) → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTNotionAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTNotionAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).


## Simulation Scenarios

These scenarios show how `GPTNotionAgent` consumes input from `InputAgent` and either:

* calls Notion when a workspace operation is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTNotionAgent
python agents/agent_GPTNotionAgent/agent.py
```

### Scenario A — InputAgent, inspect a specific page by ID

In Terminal 2 (`InputAgent`), type:

```text
> What can you tell me about the page: 202996a6195e80faac16e9ca2209c07d
```

`GPTNotionAgent` should:

1. Receive this as `content` (a string).

2. Have GPT recognize a Notion page/block ID and produce something like:

   ```json
   {
     "action": "block_children",
     "block_id": "202996a6-195e-80fa-ac16-e9ca2209c07d",
     "page_size": 50
   }
   ```

3. Call the Notion API to retrieve the children of that page.

4. Return a payload similar to:

```log
[Received] {
  'tool': 'notion',
  'performed_call': True,
  'result': {
    'action': 'block_children',
    'block_id': '202996a6-195e-80fa-ac16-e9ca2209c07d',
    'page_size': 50,
    'status': 200,
    'data': {
      'object': 'list',
      'results': [
        { 'object': 'block', 'id': '...', 'type': 'heading_1', 'heading_1': { 'rich_text': [ ... ] } },
        { 'object': 'block', 'id': '...', 'type': 'paragraph', 'paragraph': { 'rich_text': [ ... ] } },
        ...
      ],
      'has_more': False,
      'next_cursor': None
    },
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'block_children',
    'block_id': '202996a6-195e-80fa-ac16-e9ca2209c07d',
    'page_size': 50
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

You can then have another downstream agent (or yourself) interpret the returned `data.results` to generate a human-readable report on the page sections (headings, paragraphs, bullet lists, etc.).

### Scenario B — InputAgent, search Notion for a topic

In Terminal 2, type:

```text
> Search in my Notion workspace for pages (only) about investors and show me up to 5 results.
```

`GPTNotionAgent` should interpret this as a search request and GPT should output tool args similar to:

```jsonc
{
  "action": "search",
  "query": "investors",
  "page_size": 5,
  "filter_object": "page"
}
```

The agent calls `POST /v1/search` and returns:

```log
[Received] {
  'tool': 'notion',
  'performed_call': True,
  'result': {
    'action': 'search',
    'query': 'External tasks',
    'page_size': 5,
    'status': 200,
    'data': {
      'object': 'list',
      'results': [
        {
          'object': 'page',
          'id': '...',
          'url': 'https://www.notion.so/...'
          // other Notion page properties...
        },
        ...
      ],
      'has_more': False,
      'next_cursor': None
    },
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'search',
    'query': 'External tasks',
    'page_size': 5,
    'filter_object': 'page'
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

This is useful if you keep project reports or task dashboards in Notion and want a later agent to summarize or cross-reference them.

### Scenario C — InputAgent, no Notion call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain in simple terms what Notion is."}
```

Here the user is asking for a general explanation, not an operation on your workspace. The `format_prompt` tells GPT to only request a Notion call when an actual workspace search/query/list clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call Notion, and the response will look like:

```log
[Received] {
  'tool': 'notion',
  'performed_call': False,
  'result': {
    'error': 'no_notion_call_requested_or_missing_action',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these three scenarios to verify:

* that Notion is called when the intent is clearly “search/query/list something inside my Notion workspace” (even with raw page IDs or different field names), and
* that no call is made when the request is purely explanatory and does not require workspace access.
