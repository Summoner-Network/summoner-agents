# `GPTArxivAgent`

A guarded GPT powered agent that decides whether to call the **arXiv API** and, when appropriate, returns a structured list of recent papers. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an async `arxiv_search_summaries` helper. If no arXiv call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API,
* call the **arXiv API** via `aiohttp` and return normalized results.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an API call to the arXiv API to fulfill arXiv search requests.

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

   * an **empty object** `{}` meaning *do not call arXiv*, or
   * a dict with fields that match the arXiv helper signature, e.g.:

     ```json
     {"query": "all:\"fully homomorphic encryption\"", "max_results": 10}
     ```

   The send handler then:

   * checks if `tool_args` is non empty and contains a non empty `"query"` string,
   * if yes, calls:

     ```python
     api_result = await arxiv_search_summaries(
         query=tool_args["query"],
         max_results=tool_args.get("max_results", 5),
     )
     performed_call = True
     ```
   * if no, it sets:

     ```python
     api_result = {
         "error": "no_arxiv_call_requested",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "arxiv",
     "performed_call": true,
     "result": {
       "query": "<normalized query>",
       "max_results": 5,
       "count": 5,
       "results": [
         {
           "id": "2501.01234",
           "title": "Example paper title",
           "authors": ["First Author", "Second Author"],
           "published": "2025-01-15",
           "summary_snippet": "Short summary...",
           "pdf_link": "https://arxiv.org/pdf/2501.01234.pdf"
         },
         ...
       ],
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "query": "all:\"fully homomorphic encryption\"",
       "max_results": 5
     },
     "to": "<uuid of sender>"
   }
   ```

   If no call is performed, `result` contains an error payload and `performed_call` is `false`.

   Logs a summary:

   ```text
   [respond] model=<model> id=<uuid> cost=<usd_or_none> performed_call=<True|False>
   ```

9. Sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>


## SDK Features Used

| Feature                               | Description                                                                                      |
| ------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `class MyAgent(SummonerClient)`       | Subclasses `SummonerClient` to load configs, identity, and manage state                          |
| `@agent.hook(Direction.RECEIVE)`      | Validates or drops incoming messages before main handling                                        |
| `@agent.hook(Direction.SEND)`         | Signs outgoing messages by adding a `from` field with UUID                                       |
| `@agent.receive(route=...)`           | Buffers validated messages into the queue                                                        |
| `@agent.send(route=...)`              | Builds the GPT prompt, interprets output as tool args, conditionally calls arXiv, returns result |
| `agent.logger`                       | Logs hook activity, buffering, arXiv calls, and send lifecycle events                            |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                       |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                         |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTArxivAgent/`.

A **typical `gpt_config.json` for GPTArxivAgent** (only the important parts):

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

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"query\", \"topic\", \"instruction\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking to search for scientific articles on arXiv.org, or would clearly benefit from such a search (for example: \"recent papers on ...\", \"arxiv\", \"latest results on ...\", etc.).\n2) If an arXiv search IS appropriate, construct arguments for the arXiv helper function and OUTPUT a JSON object with the following keys:\n   - \"query\": a STRING suitable for the arXiv \"search_query\" parameter.\n   - \"max_results\": an INTEGER between 1 and 50 (optional; if missing, the code will default to 5).\n3) If an arXiv search is NOT appropriate, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call arXiv, you MUST include the key \"query\". You MAY include \"max_results\" if it is clearly implied; otherwise omit it.\n- Do NOT include any keys other than \"query\" and \"max_results\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not perform the search yourself; you only prepare the parameters.\n\nExamples:\n- User asks: \"Find recent papers on fully homomorphic encryption on arxiv\" → {\"query\": \"all:\\\"fully homomorphic encryption\\\"\", \"max_results\": 10}\n- User asks: \"Explain what fully homomorphic encryption is\" (no external search required) → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTArxivAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTArxivAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).


## Simulation Scenarios

These scenarios show how `GPTArxivAgent` consumes structured input from `InputAgent` and either:

- calls arXiv when a search is appropriate, or  
- returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTArxivAgent
python agents/agent_GPTArxivAgent/agent.py
```

### Scenario A — InputAgent, simple arXiv query

In Terminal 2 (`InputAgent`), type:

```txt
> Find recent papers on transformer-based fully homomorphic encryption on arxiv. Limit to around 5 results.
```

`GPTArxivAgent` should:

1. Receive this as `content`.
2. Have GPT produce something like:

   ```json
   {"query":"all:\"transformer fully homomorphic encryption\"","max_results":5}
   ```
3. Call the arXiv API with those arguments.
4. Return a payload similar to:

```log
[Received] {
  'tool': 'arxiv',
  'performed_call': True,
  'result': {
    'query': 'all:"transformer fully homomorphic encryption"',
    'max_results': 5,
    'count': 2,
    'results': [
      {
        'id': '2501.01234',
        'title': 'Transformer-based Fully Homomorphic Encryption',
        'authors': ['First Author', 'Second Author'],
        'published': '2025-01-15',
        'summary_snippet': 'Short summary...',
        'pdf_link': 'https://arxiv.org/pdf/2501.01234.pdf'
      },
      ...
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'query': 'all:"transformer fully homomorphic encryption"',
    'max_results': 5
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

### Scenario B — InputAgent, arXiv query in a different shape

Here we vary the shape of the request to test the prompt’s robustness. In Terminal 2, type:

```txt
> {"topic":"recent advances in lattice-based fully homomorphic encryption","hint":"if needed, you can search on arxiv for the latest work","limit":3}
```

Even though the keys are different (`topic`, `hint`, `limit` instead of `question` / `query`), GPT should interpret this as an arXiv search request and produce arguments like:

```json
{"query":"all:\"lattice-based fully homomorphic encryption\"","max_results":3}
```

You should again see a response where:

* `performed_call` is `True`,
* `result.results` is a list of up to 3 papers,
* `tool_args.max_results` reflects the inferred limit (`3`).

### Scenario C — InputAgent, no arXiv call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain in simple terms what fully homomorphic encryption is."}
```

Here the user did not ask for a search or mention arXiv explicitly. The `format_prompt` tells GPT to only request an arXiv call when a search clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call arXiv, and the response will look like:

```log
[Received] {
  'tool': 'arxiv',
  'performed_call': False,
  'result': {
    'error': 'no_arxiv_call_requested',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these three scenarios to verify:

* that arXiv is called when the intent is clearly “search arxiv” (even with different field names), and
* that no call is made when the request is purely explanatory.

