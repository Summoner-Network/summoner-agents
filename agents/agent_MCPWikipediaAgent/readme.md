# `MCPWikipediaAgent`

A guarded GPT powered agent that decides whether to call the **Wikipedia MCP tool** and, when appropriate, returns a structured result for a **title search**, a **direct page summary**, or a **search-then-summary**. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an MCP tool call to `wikipedia_handle_request`. If no Wikipedia call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive and send hooks with a buffer,
* integrate cost and token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call a tool and which operation to run,
* call the **Wikipedia REST API** indirectly via an **MCP server** (which uses `aiohttp`) and return normalized results.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (for example [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an MCP tool layer for Wikipedia search and summary requests.

> [!IMPORTANT]
> **OpenAI credentials required.** Both agents call `load_dotenv()` and expect an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it's available at runtime:
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
> **No Wikipedia API key is required.** `MCPWikipediaAgent` calls an MCP server that uses public Wikipedia REST endpoints (title search and page summary). The MCP server sets a proper `User-Agent` header when it creates its `aiohttp.ClientSession`, so you do not need to configure anything else for Wikipedia access.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),

   * GPT config from `gpt_config.json` (or `--gpt <path>`), including:

     * `model`, `output_parsing`, `max_chat_input_tokens`, `max_chat_output_tokens`,
     * `personality_prompt`, `format_prompt`,
     * `sleep_seconds`, `cost_limit_usd`, `debug`,

   * an MCP endpoint URL (`mcp_url`) from `gpt_config.json` (or environment), typically:

     ```text
     http://localhost:8000/mcp
     ```

   * an identity UUID (`my_id`) from `id.json` (or `--id <path>`).

3. Incoming messages invoke the receive hook (`@agent.hook(Direction.RECEIVE)`):

   * If it is a string starting with `"Warning:"`, the agent logs a warning and drops it.
   * If it is not a dict with `"remote_addr"` and `"content"`, it logs

     ```text
     [hook:recv] missing address/content
     ```

     and drops it.
   * Otherwise, it logs

     ```text
     [hook:recv] <addr> passed validation
     ```

     and forwards the message to the receive handler.

4. The receive handler (`@agent.receive(route="")`) enqueues `msg["content"]` into `message_buffer` and logs:

   ```text
   Buffered message from:(SocketAddress=<addr>).
   ```

5. Before sending, the send hook (`@agent.hook(Direction.SEND)`) logs:

   ```text
   [hook:send] sign <uuid>
   ```

   It wraps raw strings into `{"message": ...}`, adds `{"from": my_id}`, and forwards the message to the send handler.

6. The send handler (`@agent.send(route="")`) dequeues the payload (`content`) and builds a single user message:

   ```text
   <personality_prompt>
   <format_prompt>

   Content:
   <JSON-serialized payload>
   ```

   This works whether `content` is a raw string or a JSON like object. In the string case it is serialized as a JSON string.

   It then:

   1. Calls `gpt_call_async(...)` with `output_parsing="json"`.
   2. Interprets the GPT output as a tool argument dictionary `tool_args`:

      * If GPT returns a string, it tries to `json.loads` it.
      * If the result is not a dict, it falls back to `{}`.

7. The GPT output is expected to be either:

   * an empty object `{}` meaning do not call Wikipedia, or
   * a dict with fields describing a Wikipedia operation, for example:

     ```json
     {"action": "search_titles", "query": "fully homomorphic encryption", "limit": 5}
     ```

     or

     ```json
     {"action": "summary", "title": "Category theory"}
     ```

     or

     ```json
     {"action": "search_summary", "query": "key switching in homomorphic encryption", "limit": 5}
     ```

   The send handler then:

   * checks if `tool_args` is non empty and contains a non empty `"action"` string,

   * if yes, calls the MCP tool:

     ```python
     mcp_raw = await mcp_call_tool(
         mcp_url=agent.mcp_url,
         tool_name="wikipedia_handle_request",
         arguments={"tool_args": tool_args},
     )
     api_result = unwrap_mcp_result(mcp_raw)
     performed_call = True
     ```

   * if no, it sets:

     ```python
     api_result = {
         "error": "no_wikipedia_call_requested_or_missing_action",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "wikipedia",
     "performed_call": true,
     "result": {
       "action": "search_titles",
       "query": "fully homomorphic encryption",
       "lang": "en",
       "limit": 5,
       "count": 3,
       "pages": [
         {
           "title": "Fully homomorphic encryption",
           "description": "Class of encryption schemes...",
           "key": "Fully_homomorphic_encryption",
           "url": "https://en.wikipedia.org/wiki/Fully_homomorphic_encryption"
         },
         {
           "title": "Homomorphic encryption",
           "description": "Form of encryption...",
           "key": "Homomorphic_encryption",
           "url": "https://en.wikipedia.org/wiki/Homomorphic_encryption"
         }
       ],
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "action": "search_titles",
       "query": "fully homomorphic encryption",
       "limit": 5
     },
     "to": "<uuid of sender>"
   }
   ```

   If the MCP tool detects missing parameters or no suitable pages, `result` contains an error payload, for example:

   ```json
   {
     "error": "no_pages_found",
     "action": "search_summary",
     "query": "unknown topic ...",
     "lang": "en",
     "limit": 5,
     "timestamp_utc": "2025-12-04T12:00:00Z"
   }
   ```

   The agent logs a summary:

   ```text
   [respond] model=<model> id=<uuid> cost=<usd_or_none> performed_call=<True|False>
   ```

9. The agent sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                              | Description                                                                                            |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                                |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                              |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                             |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                              |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls an MCP tool, returns result |
| `agent.logger`                       | Logs hook activity, buffering, tool calls, and send lifecycle events                                   |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                             |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                               |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Start the Wikipedia MCP server (the tool provider):

```bash
python agents/agent_MCPWikipediaAgent/mcp_server.py
```

Prepare `gpt_config.json` and `id.json` in `agents/agent_MCPWikipediaAgent/`.

A typical `gpt_config.json` for `MCPWikipediaAgent` (only the important parts) is:

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

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"topic\", \"title\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking for information that should be looked up on Wikipedia, or would clearly benefit from a Wikipedia-based summary. Cues include explicit mentions of Wikipedia, encyclopedic topics (for example historical events, scientific concepts, organizations, people), or requests for a concise overview of a named concept.\n2) If a Wikipedia lookup IS appropriate and you can identify the necessary parameters, choose exactly ONE of the following actions and OUTPUT a JSON object with the required keys:\n\n   A) Title search – when the user wants a short list of matching pages:\n      - Set \"action\": \"search_titles\".\n      - Set \"query\": a STRING with the search text.\n      - Optionally set \"limit\": an INTEGER between 1 and 50 (default behavior will be 5 if omitted).\n      - Optionally set \"lang\": a 2-letter language code such as \"en\" or \"fr\" if the user clearly requests a specific language. If not mentioned, omit it and English will be used.\n\n   B) Direct summary – when the user clearly gives an exact page title:\n      - Set \"action\": \"summary\".\n      - Set \"title\": the page title as a STRING (for example: \"Fully homomorphic encryption\").\n      - Optionally set \"lang\" as above.\n\n   C) Search then summary – when the user describes a topic in natural language and wants an explanation or overview, but does not give a precise title:\n      - Set \"action\": \"search_summary\".\n      - Set \"query\": a STRING describing the topic.\n      - Optionally set \"limit\": an INTEGER between 1 and 50 (default behavior will be 5 if omitted). This controls how many titles are considered in the search. The helper will summarize the top match.\n      - Optionally set \"lang\" as above.\n\n3) If a Wikipedia lookup is NOT appropriate, or if you cannot reliably infer the required parameters, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call Wikipedia, you MUST include the key \"action\" and the keys required for that action (for example, \"query\" for search_titles or search_summary, \"title\" for summary).\n- You MAY include optional keys like \"limit\" or \"lang\" when the user's request clearly implies them; otherwise omit them.\n- Do NOT include any keys other than: \"action\", \"query\", \"title\", \"limit\", \"lang\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not call the API; you only prepare the parameters.\n\nExamples:\n- User asks: \"Search Wikipedia for pages about fully homomorphic encryption and show me a few options\" → {\"action\": \"search_titles\", \"query\": \"fully homomorphic encryption\", \"limit\": 5}\n- User asks: \"Give me the summary of the Wikipedia page for Fully homomorphic encryption\" → {\"action\": \"summary\", \"title\": \"Fully homomorphic encryption\"}\n- User asks: \"Explain, using Wikipedia, what key switching in homomorphic encryption is\" → {\"action\": \"search_summary\", \"query\": \"key switching in homomorphic encryption\"}\n- User asks: \"What is your opinion about this topic?\" with no encyclopedic topic specified → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_MCPWikipediaAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_MCPWikipediaAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner client config path (defaults to `configs/client_config.json`).

## Simulation Scenarios

These scenarios show how `MCPWikipediaAgent` consumes input from `InputAgent` and either:

* calls the Wikipedia MCP tool when a lookup is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: Wikipedia MCP server
python agents/agent_MCPWikipediaAgent/mcp_server.py

# Terminal 4: MCPWikipediaAgent
python agents/agent_MCPWikipediaAgent/agent.py
```

### Scenario A – InputAgent, title search

In Terminal 2 (`InputAgent`), type:

```text
> Search Wikipedia for pages about fully homomorphic encryption and show me a few options.
```

`MCPWikipediaAgent` should:

1. Receive this as `content` (a string).

2. Have GPT produce something like:

   ```json
   {"action": "search_titles", "query": "fully homomorphic encryption", "limit": 5}
   ```

3. Call the MCP tool `wikipedia_handle_request` with those arguments.

4. Return a payload similar to:

```log
[Received] {
  'tool': 'wikipedia',
  'performed_call': True,
  'result': {
    'action': 'search_titles',
    'query': 'fully homomorphic encryption',
    'lang': 'en',
    'limit': 5,
    'count': 2,
    'pages': [
      {
        'title': 'Fully homomorphic encryption',
        'description': 'Class of encryption schemes...',
        'key': 'Fully_homomorphic_encryption',
        'url': 'https://en.wikipedia.org/wiki/Fully_homomorphic_encryption'
      },
      {
        'title': 'Homomorphic encryption',
        'description': 'Form of encryption...',
        'key': 'Homomorphic_encryption',
        'url': 'https://en.wikipedia.org/wiki/Homomorphic_encryption'
      }
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'search_titles',
    'query': 'fully homomorphic encryption',
    'limit': 5
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

You or a downstream agent can then decide which page to inspect further.

### Scenario B – InputAgent, direct page summary

In Terminal 2, type:

```text
> Give me the summary of the Wikipedia page for Category theory.
```

`MCPWikipediaAgent` should interpret this as a direct summary request and GPT should output tool args similar to:

```json
{"action": "summary", "title": "Category theory"}
```

The agent calls the MCP tool and returns:

```log
[Received] {
  'tool': 'wikipedia',
  'performed_call': True,
  'result': {
    'action': 'summary',
    'title': 'Category theory',
    'lang': 'en',
    'description': 'Branch of mathematics...',
    'summary': 'Category theory is a branch of mathematics that...',
    'url': 'https://en.wikipedia.org/wiki/Category_theory',
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'summary',
    'title': 'Category theory'
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

This is useful if another agent wants to give a short explanation grounded in Wikipedia.

### Scenario C – InputAgent, search then summary for a vague topic

In Terminal 2, type:

```text
> Using Wikipedia, explain what key switching in homomorphic encryption is.
```

`MCPWikipediaAgent` should interpret this as a search then summary request and GPT should output tool args like:

```json
{"action": "search_summary", "query": "key switching in homomorphic encryption", "limit": 5}
```

The agent will:

1. Call the MCP tool to search for titles matching the query.
2. Take the top match.
3. Fetch the summary for that page.

You should see a response where:

* `performed_call` is `True`,
* `result.action` is `"search_summary"`,
* `result.search.pages` lists the candidate pages,
* `result.summary` contains the summary for the chosen top title.

### Scenario D – InputAgent, no Wikipedia call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain in simple terms what unit tests are."}
```

Here the user is asking a general programming question, not specifically about Wikipedia. The `format_prompt` tells GPT to only request a Wikipedia call when an encyclopedic lookup clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call Wikipedia, and the response will look like:

```log
[Received] {
  'tool': 'wikipedia',
  'performed_call': False,
  'result': {
    'error': 'no_wikipedia_call_requested_or_missing_action',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these scenarios to verify:

* that the MCP tool is called when the intent is clearly a Wikipedia style lookup (search, summary, or search then summary), and
* that no tool call is made when the request is purely explanatory and does not require Wikipedia.
