You're right. My previous edit didn't go far enough, and I missed the renaming consistency.

Below is a **minimal, careful conversion** of your README that:

* Renames **GPTRedditAgent → MCPRedditAgent** everywhere it matters.
* Explicitly states the **MCP flow**: GPT decides tool args, then the agent calls the MCP tool `reddit_handle_request`.
* Keeps your structure and wording, changing only what is required.
* Updates run instructions to include the MCP server and the extra terminal.

---

# `MCPRedditAgent`

A guarded GPT powered agent that decides whether to call the **Reddit API** and, when appropriate, returns a structured response for a **subreddit listing**, a **search**, or a **comment listing** on a specific thread. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an MCP tool named `reddit_handle_request`. If no Reddit call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API and which Reddit operation to run,
* call a **local MCP server** exposing the `reddit_handle_request` tool (implemented using [`Async PRAW`](https://asyncpraw.readthedocs.io/en/latest/)) and return normalized results.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and replacing the direct Reddit call layer with an MCP tool call layer (to a local MCP server) to fulfill subreddit / search / comment lookup requests.

> [!IMPORTANT]
> **OpenAI credentials required.** The agent calls `load_dotenv()` and expects an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it's available at runtime:
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
> **Reddit credentials required (used by the MCP server).**
> To let `MCPRedditAgent` retrieve Reddit content, you must create a Reddit "script" application and store the credentials in your `.env` (the MCP server reads these):
>
> 1. Go to your Reddit app preferences: [https://www.reddit.com/prefs/apps/](https://www.reddit.com/prefs/apps/).
> 2. Click **create application** at the bottom.
> 3. Fill the form:
>
>    * **name**: any label, e.g. `Summoner Reddit bot`.
>    * Select **script**:
>
>      * `[ ] web app`
>      * `[ ] installed app`
>      * `[x] script    Script for personal use. Will only have access to the developer's accounts`
>    * **description**: optional.
>    * **about url**: optional.
>    * **redirect uri**: for a script app you can use `http://localhost` (it is still required).
>    * Click **[create app]**.
> 4. After creation, Reddit shows:
>
>    * a **client ID** (the short string under the app name),
>    * a **client secret**,
>    * you already have your **Reddit username** and **password**.
> 5. Store them in your `.env`:
>
>    ```env
>    REDDIT_CLIENT_ID=your_client_id_here
>    REDDIT_CLIENT_SECRET=your_client_secret_here
>    REDDIT_USERNAME=your_reddit_username
>    REDDIT_PW=your_reddit_password
>    # optional, but recommended:
>    REDDIT_USER_AGENT=summoner-reddit-bot by u/your_reddit_username
>    ```
>
> Ensure your `.env` is in `.gitignore`.
> If any of these are missing, the MCP tool returns a `missing_reddit_credentials` error when it tries to call Reddit.

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

   * An identity UUID (`my_id`) from `id.json` (or `--id <path>`),

   * An MCP URL (e.g. `http://127.0.0.1:8000/mcp`) to call the Reddit MCP tool.

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

   * an **empty object** `{}` meaning *do not call Reddit*, or
   * a dict describing a Reddit operation (same schema as before):

     ```json
     {
       "action": "search",
       "query": "multi-agent reinforcement learning",
       "subreddit": "all",
       "sort": "relevance",
       "time_filter": "week",
       "limit": 20
     }
     ```

   The send handler then:

   * checks if `tool_args` is non-empty and contains a non-empty `"action"` string,

   * if yes, calls the MCP tool:

     ```python
     mcp_raw = await mcp_call_tool(
         mcp_url=agent.mcp_url,
         tool_name="reddit_handle_request",
         arguments={"tool_args": tool_args},
     )
     api_result = unwrap_mcp_result(mcp_raw)
     performed_call = True
     ```

   * if no, it sets:

     ```python
     api_result = {
         "error": "no_reddit_call_requested_or_missing_action",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The `reddit_handle_request` MCP tool (served by the MCP server) internally:

   * creates an `asyncpraw.Reddit(...)` client using your credentials,
   * dispatches based on `action` (`subreddit_posts`, `search`, `comments`),
   * normalizes submissions/comments to the same shapes as before,
   * wraps the core payload with `timestamp_utc`.

9. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "reddit",
     "performed_call": true,
     "result": { ... },
     "tool_args": { ... },
     "to": "<uuid of sender>"
   }
   ```

   If Reddit credentials are missing or the action is unsupported, `result` contains an error payload (e.g. `missing_reddit_credentials`, `missing_query`, `missing_subreddit`, `missing_submission_reference`).

   The agent logs a summary:

   ```text
   [respond] model=<model> id=<uuid> cost=<usd_or_none> performed_call=<True|False>
   ```

10. Sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                              | Description                                                                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                                                         |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                                                       |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                                                      |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                                                       |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls the MCP tool `reddit_handle_request`, returns result |
| `agent.logger`                       | Logs hook activity, buffering, MCP calls, and send lifecycle events                                                             |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                                                      |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                                                        |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Start the Reddit MCP server (the tool provider):

```bash
python agents/agent_MCPRedditAgent/mcp_server.py
```

Prepare `gpt_config.json` and `id.json` in `agents/agent_MCPRedditAgent/`.

A **typical `gpt_config.json` for MCPRedditAgent** (only the important parts):

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

  "format_prompt": "..."
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_MCPRedditAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_MCPRedditAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).

## Simulation Scenarios

These scenarios show how `MCPRedditAgent` consumes input from `InputAgent` and either:

* calls Reddit (via the MCP server) when a subreddit / search / comment lookup is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: Reddit MCP server
python agents/agent_MCPRedditAgent/mcp_server.py

# Terminal 4: MCPRedditAgent
python agents/agent_MCPRedditAgent/agent.py
```


### Scenario A — InputAgent, latest posts from a specific subreddit

In Terminal 2 (`InputAgent`), type:

```text
> Show me the latest 5 posts from r/AI_agents.
```

`GPTRedditAgent` should:

1. Receive this as `content` (a string).

2. Have GPT produce something like:

   ```json
   {
     "action": "subreddit_posts",
     "subreddit": "AI_agents",
     "sort": "new",
     "limit": 5
   }
   ```

3. Call Reddit via Async PRAW (subreddit listing with `sort=new`, `limit=5`).

4. Return a payload similar to:

```log
[Received] {
  'tool': 'reddit',
  'performed_call': True,
  'result': {
    'action': 'subreddit_posts',
    'subreddit': 'AI_agents',
    'sort': 'new',
    'limit': 5,
    'query': null,
    'count': 5,
    'posts': [
      {
        'id': 'abc123',
        'title': 'New multi-agent coordination framework',
        'subreddit': 'AI_agents',
        'author': 'some_user',
        'score': 42,
        'num_comments': 10,
        'created_utc': 1733312345.0,
        'url': 'https://example.com/...',
        'permalink': 'https://www.reddit.com/r/AI_agents/comments/abc123/...',
        'selftext_snippet': 'Short snippet of the body...'
      },
      ...
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'subreddit_posts',
    'subreddit': 'AI_agents',
    'sort': 'new',
    'limit': 5
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```


### Scenario B — InputAgent, search Reddit for a topic

In Terminal 2, type:

```text
> Search Reddit for discussions about multi-agent reinforcement learning in any subreddit. I care about the most relevant posts from the past week, up to 10 results.
```

`GPTRedditAgent` should interpret this as a search and GPT should output tool args similar to:

```json
{
  "action": "search",
  "query": "multi-agent reinforcement learning",
  "subreddit": "all",
  "sort": "relevance",
  "time_filter": "week",
  "limit": 10
}
```

The agent calls `subreddit('all').search(...)` and returns:

```log
[Received] {
  'tool': 'reddit',
  'performed_call': True,
  'result': {
    'action': 'search',
    'query': 'multi-agent reinforcement learning',
    'subreddit': 'all',
    'sort': 'relevance',
    'time_filter': 'week',
    'limit': 10,
    'count': 10,
    'posts': [
      {
        'id': 'xyz789',
        'title': 'Experience using multi-agent RL in production',
        'subreddit': 'machinelearning',
        'author': 'another_user',
        'score': 150,
        'num_comments': 60,
        'created_utc': 1733312000.0,
        'url': 'https://example.com/...',
        'permalink': 'https://www.reddit.com/r/machinelearning/comments/xyz789/...',
        'selftext_snippet': 'Short snippet...'
      },
      ...
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'search',
    'query': 'multi-agent reinforcement learning',
    'subreddit': 'all',
    'sort': 'relevance',
    'time_filter': 'week',
    'limit': 10
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

This scenario checks the mapping:

* "most relevant" → `sort="relevance"`,
* "past week" → `time_filter="week"`,
* "up to 10 results" → `limit=10`.


### Scenario C — InputAgent, comments on a specific Reddit thread

In Terminal 2, type:

```text
> Show me the top 20 comments from this thread: https://www.reddit.com/r/AI_agents/comments/abc123/some_discussion_title/
```

`GPTRedditAgent` should interpret this as a comments request, and GPT should output something like:

```json
{
  "action": "comments",
  "submission_url": "https://www.reddit.com/r/AI_agents/comments/abc123/some_discussion_title/",
  "sort": "top",
  "limit": 20
}
```

The agent calls `reddit.submission(url=...)`, flattens comments, and returns:

```log
[Received] {
  'tool': 'reddit',
  'performed_call': True,
  'result': {
    'action': 'comments',
    'submission_id': 'abc123',
    'submission_url': 'https://www.reddit.com/r/AI_agents/comments/abc123/some_discussion_title/',
    'sort': 'top',
    'limit': 20,
    'count': 20,
    'comments': [
      {
        'id': 'c_def456',
        'author': 'some_commenter',
        'score': 120,
        'created_utc': 1733312100.0,
        'body': 'Interesting observation about agent coordination...',
        'permalink': 'https://www.reddit.com/r/AI_agents/comments/abc123/.../c_def456/'
      },
      ...
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'comments',
    'submission_url': 'https://www.reddit.com/r/AI_agents/comments/abc123/some_discussion_title/',
    'sort': 'top',
    'limit': 20
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

You can then have another downstream agent (or yourself) summarize these comments, extract pros/cons, etc.


### Scenario D — InputAgent, no Reddit call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain in simple terms what Reddit is and how subreddits work."}
```

Here the user is asking for a general explanation, not a lookup on Reddit. The `format_prompt` tells GPT to only request a Reddit call when an actual subreddit listing/search/comments retrieval clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call Reddit, and the response will look like:

```log
[Received] {
  'tool': 'reddit',
  'performed_call': False,
  'result': {
    'error': 'no_reddit_call_requested_or_missing_action',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these scenarios to verify:

* that Reddit is called when the intent is clearly "show posts / search / fetch comments on Reddit", and that the call is executed via the MCP tool `reddit_handle_request`, and
* that no call is made (and therefore no MCP tool call is attempted) when the request is purely explanatory and does not require contacting Reddit.

