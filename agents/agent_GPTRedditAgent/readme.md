# `GPTRedditAgent`

A guarded GPT powered agent that decides whether to call the **Reddit API** and, when appropriate, returns a structured response for a **subreddit listing**, a **search**, or a **comment listing** on a specific thread. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an async `reddit_handle_request` helper. If no Reddit call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API and which Reddit operation to run,
* call the **Reddit API** via [`Async PRAW`](https://asyncpraw.readthedocs.io/en/latest/) and return normalized results.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an API call layer to Reddit (via Async PRAW) to fulfill subreddit / search / comment lookup requests.

> [!IMPORTANT]
> **OpenAI credentials required.** Both agents call `load_dotenv()` and expect an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it's available at runtime:
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
> **Reddit credentials required to access Reddit via Async PRAW.**
> To let `GPTRedditAgent` talk to Reddit, you must create a Reddit “script” application and store the credentials in your `.env`:
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
> If any of these are missing, the agent will return a `missing_reddit_credentials` error when it tries to call Reddit.


## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),

   * Reddit credentials from `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PW` (and optionally `REDDIT_USER_AGENT`),

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

   * an **empty object** `{}` meaning *do not call Reddit*, or
   * a dict describing a Reddit operation, for example:

     ```json
     {
       "action": "subreddit_posts",
       "subreddit": "AI_agents",
       "sort": "new",
       "limit": 5
     }
     ```

     or

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

     or

     ```json
     {
       "action": "comments",
       "submission_url": "https://www.reddit.com/r/AI_agents/comments/abc123/...",
       "sort": "top",
       "limit": 30
     }
     ```

   The send handler then:

   * checks if `tool_args` is non-empty and contains a non-empty `"action"` string,
   * if yes, calls:

     ```python
     api_result = await reddit_handle_request(tool_args)
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

8. The `reddit_handle_request` helper internally:

   * creates an `asyncpraw.Reddit(...)` client using your credentials,

   * dispatches based on `action`:

     * `"subreddit_posts"` → list or search posts in a specific subreddit;
     * `"search"` → Reddit search across `r/all` or a specific subreddit;
     * `"comments"` → fetch comments for a given submission,

   * normalizes submissions to:

     ```json
     {
       "id": "abc123",
       "title": "Post title",
       "subreddit": "AI_agents",
       "author": "some_user",
       "score": 123,
       "num_comments": 45,
       "created_utc": 1733312345.0,
       "url": "https://example.com",
       "permalink": "https://www.reddit.com/r/AI_agents/comments/abc123/...",
       "selftext_snippet": "Short snippet of the body..."
     }
     ```

   * normalizes comments to:

     ```json
     {
       "id": "c_def456",
       "author": "another_user",
       "score": 42,
       "created_utc": 1733312000.0,
       "body": "Comment text...",
       "permalink": "https://www.reddit.com/r/AI_agents/comments/abc123/.../c_def456/"
     }
     ```

   * wraps the core payload with a `timestamp_utc` field.

9. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "reddit",
     "performed_call": true,
     "result": {
       "action": "search",
       "query": "multi-agent reinforcement learning",
       "subreddit": "all",
       "sort": "relevance",
       "time_filter": "week",
       "limit": 10,
       "count": 10,
       "posts": [
         {
           "id": "abc123",
           "title": "Interesting discussion on multi-agent RL",
           "subreddit": "AI_agents",
           "author": "some_user",
           "score": 200,
           "num_comments": 35,
           "created_utc": 1733312345.0,
           "url": "https://example.com/...",
           "permalink": "https://www.reddit.com/r/AI_agents/comments/abc123/...",
           "selftext_snippet": "Short snippet..."
         }
       ],
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "action": "search",
       "query": "multi-agent reinforcement learning",
       "subreddit": "all",
       "sort": "relevance",
       "time_filter": "week",
       "limit": 10
     },
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

| Feature                              | Description                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                           |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                         |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                        |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                         |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls Reddit, returns result |
| `agent.logger`                       | Logs hook activity, buffering, Reddit calls, and send lifecycle events                            |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                        |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                          |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTRedditAgent/`.

A **typical `gpt_config.json` for GPTRedditAgent** (only the important parts):

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

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"subreddit\", \"query\", \"url\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking to look up content on Reddit or would clearly benefit from such a lookup. Cues include mentions of Reddit, r/<name> (e.g. \"r/AI_agents\"), \"on reddit\", \"top posts\", \"latest posts\", or an explicit Reddit thread URL.\n2) If a Reddit lookup IS appropriate and you can identify the necessary parameters, choose exactly ONE of the following actions and OUTPUT a JSON object with the required keys:\n\n   A) Subreddit listing – when the user wants posts from a specific subreddit:\n      - Set \"action\": \"subreddit_posts\".\n      - Set \"subreddit\": the subreddit name WITHOUT the \"r/\" prefix (e.g. \"AI_agents\").\n      - Optionally set \"sort\": one of \"hot\", \"new\", \"top\", \"rising\", \"controversial\". Map phrases like \"latest\" or \"newest\" to \"new\"; \"top\" or \"most upvoted\" to \"top\". If unclear, omit.\n      - Optionally set \"limit\": an integer between 1 and 50 (if the user asks for a specific number of posts). If unclear, omit.\n      - Optionally set \"query\": a string to filter posts by text within that subreddit if the user clearly specifies a topic.\n\n   B) Search – when the user wants Reddit search results for a topic (possibly across all subreddits):\n      - Set \"action\": \"search\".\n      - Set \"query\": a string with the topic to search for.\n      - Optionally set \"subreddit\":\n          * \"all\" to search across Reddit, or\n          * a specific subreddit name (e.g. \"machinelearning\") if the user clearly specifies it.\n      - Optionally set \"sort\": one of \"relevance\", \"hot\", \"new\", \"top\", \"comments\". Map phrases like \"most relevant\" or \"best match\" to \"relevance\"; \"latest\" or \"newest\" to \"new\"; \"top\" or \"most upvoted\" to \"top\".\n      - Optionally set \"time_filter\": one of \"all\", \"hour\", \"day\", \"week\", \"month\", \"year\". Map phrases like \"this week\" to \"week\", \"last 24 hours\" or \"today\" to \"day\". If unclear, omit.\n      - Optionally set \"limit\": an integer between 1 and 50.\n\n   C) Comments – when the user refers to a specific Reddit thread and wants comments:\n      - Set \"action\": \"comments\".\n      - If the user provides a Reddit URL, set \"submission_url\" to that URL.\n      - If the user provides a bare Reddit post ID, set \"submission_id\" to that ID.\n      - Optionally set \"sort\": one of \"top\", \"new\", \"controversial\", \"old\", \"qa\". Map phrases like \"top comments\" to \"top\", \"newest comments\" to \"new\".\n      - Optionally set \"limit\": an integer between 1 and 100 indicating how many comments to retrieve.\n\n3) If a Reddit lookup is NOT appropriate, or if you cannot reliably infer the required parameters, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call Reddit, you MUST include the key \"action\" and the keys required for that action (e.g., \"subreddit\" for subreddit_posts, \"query\" for search, some combination of \"submission_url\" / \"submission_id\" for comments).\n- You MAY include optional keys like \"sort\", \"time_filter\", \"limit\", or \"query\" when the user’s request clearly implies them; otherwise omit them.\n- Do NOT include any keys other than: \"action\", \"subreddit\", \"query\", \"sort\", \"time_filter\", \"limit\", \"submission_id\", \"submission_url\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not call the API; you only prepare the parameters.\n\nExamples:\n- User asks: \"Show me the latest 5 posts from r/AI_agents\" → {\"action\": \"subreddit_posts\", \"subreddit\": \"AI_agents\", \"sort\": \"new\", \"limit\": 5}\n- User asks: \"Search Reddit for discussions about multi-agent reinforcement learning in any subreddit\" → {\"action\": \"search\", \"query\": \"multi-agent reinforcement learning\", \"subreddit\": \"all\", \"sort\": \"relevance\"}\n- User asks: \"Search r/machinelearning for the top posts this week about diffusion models\" → {\"action\": \"search\", \"query\": \"diffusion models\", \"subreddit\": \"machinelearning\", \"sort\": \"top\", \"time_filter\": \"week\"}\n- User asks: \"Show me the top comments on this thread: https://www.reddit.com/r/AI_agents/comments/abc123/...\" → {\"action\": \"comments\", \"submission_url\": \"https://www.reddit.com/r/AI_agents/comments/abc123/...\", \"sort\": \"top\", \"limit\": 20}\n- User asks: \"What is Reddit?\" (no API call needed) → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTRedditAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTRedditAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).


## Simulation Scenarios

These scenarios show how `GPTRedditAgent` consumes input from `InputAgent` and either:

* calls Reddit when a subreddit / search / comment lookup is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTRedditAgent
python agents/agent_GPTRedditAgent/agent.py
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

* “most relevant” → `sort="relevance"`,
* “past week” → `time_filter="week"`,
* “up to 10 results” → `limit=10`.


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

* that Reddit is called when the intent is clearly “show posts / search / fetch comments on Reddit” (even with plain strings or different field names), and
* that no call is made when the request is purely explanatory and does not require contacting Reddit.
