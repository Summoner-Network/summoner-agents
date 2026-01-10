# `GPTGitHubAgent`

A guarded GPT powered agent that decides whether to call the **GitHub API** and, when appropriate, returns a structured summary of recent commits for a repository. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an async `github_latest_commits_summary` helper. If no GitHub call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API,
* call the **GitHub API** via `aiohttp` and return normalized results.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an API call to the GitHub API to fulfill GitHub commit lookup requests.

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
> **Optional `GITHUB_TOKEN` for higher rate limits and private repos (generate it [here](https://github.com/settings/tokens)).**
> If you set `GITHUB_TOKEN` in your `.env`, the agent will include it as a `token` header for GitHub API calls. This raises the rate limit and allows access to private repositories (depending on scopes). A minimal setup is:
>
> ```env
> GITHUB_TOKEN=ghp_yourGeneratedTokenHere
> ```
>
> Ensure your `.env` is in `.gitignore`. When `GITHUB_TOKEN` is not set, the agent still works, but with lower unauthenticated rate limits and only on public repos.


## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),
   * optionally `GITHUB_TOKEN` from environment (via the same `.env`),
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

   * an **empty object** `{}` meaning *do not call GitHub*, or
   * a dict with fields that match the GitHub helper signature, e.g.:

     ```json
     {"owner": "Summoner-Network", "repo": "agent-sdk", "max_commits": 5}
     ```

   The send handler then:

   * checks if `tool_args` contains non empty `owner` and `repo` strings,
   * if yes, calls:

     ```python
     api_result = await github_latest_commits_summary(
         owner=tool_args["owner"],
         repo=tool_args["repo"],
         max_commits=tool_args.get("max_commits", 5),
     )
     performed_call = True
     ```
   * if no, it sets:

     ```python
     api_result = {
         "error": "no_github_call_requested_or_missing_owner_repo",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "github",
     "performed_call": true,
     "result": {
       "owner": "Summoner-Network",
       "repo": "agent-sdk",
       "max_commits": 5,
       "count": 3,
       "commits": [
         {
           "sha": "abcd1234...",
           "short_sha": "abcd123",
           "author": "First Author",
           "date": "2025-01-15T12:34:56Z",
           "subject": "Short commit subject",
           "message": "Full commit message\nwith multiple lines...",
           "html_url": "https://github.com/Summoner-Network/agent-sdk/commit/abcd1234...",
           "stats": { "additions": 10, "deletions": 2, "total": 12 },
           "files": [
             {
               "filename": "src/main.py",
               "additions": 5,
               "deletions": 1,
               "changes": 6
             }
           ]
         },
         ...
       ],
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "owner": "Summoner-Network",
       "repo": "agent-sdk",
       "max_commits": 5
     },
     "to": "<uuid of sender>"
   }
   ```

   If no call is performed, `result` contains an error payload and `performed_call` is `false`.

   The agent logs a summary:

   ```text
   [respond] model=<model> id=<uuid> cost=<usd_or_none> performed_call=<True|False>
   ```

9. Sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>


## SDK Features Used

| Feature                              | Description                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage state                           |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                         |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                        |
| `@agent.receive(route=...)`          | Buffers validated messages into the queue                                                         |
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls GitHub, returns result |
| `agent.logger`                       | Logs hook activity, buffering, GitHub calls, and send lifecycle events                            |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                        |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                          |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTGitHubAgent/`.

A **typical `gpt_config.json` for GPTGitHubAgent** (only the important parts):

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

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"owner\", \"repo\", or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking for information about recent commits to a specific GitHub repository, or would clearly benefit from such information. Typical cues include phrases like \"recent commits\", \"latest changes\", \"what changed in\", or an explicit GitHub repo path like \"user/repo\".\n2) If a GitHub commits lookup IS appropriate and the repository owner and name can be identified, construct arguments for the GitHub helper function and OUTPUT a JSON object with the following keys:\n   - \"owner\": a STRING with the GitHub username or organization (for example: \"Summoner-Network\").\n   - \"repo\": a STRING with the repository name (for example: \"agent-sdk\").\n   - \"max_commits\": an INTEGER between 1 and 20 (optional; if missing, the code will default to 5).\n3) If a GitHub commits lookup is NOT appropriate, or if you cannot reliably infer BOTH owner and repo, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call GitHub, you MUST include both keys \"owner\" and \"repo\". You MAY include \"max_commits\" if the request implies a specific number (e.g., \"show me the last 3 commits\"); otherwise omit it.\n- Do NOT include any keys other than \"owner\", \"repo\", and \"max_commits\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not call the API; you only prepare the parameters.\n\nExamples:\n- User asks: \"Show me the last 5 commits for Summoner-Network/agent-sdk\" → {\"owner\": \"Summoner-Network\", \"repo\": \"agent-sdk\", \"max_commits\": 5}\n- User asks: \"What is the purpose of the agent-sdk repo on GitHub?\" (no explicit request for recent commits) → {}\n- User asks: \"What changed recently in the Summoner-Network/agent-sdk repository?\" → {\"owner\": \"Summoner-Network\", \"repo\": \"agent-sdk\"}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTGitHubAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTGitHubAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).


## Simulation Scenarios

These scenarios show how `GPTGitHubAgent` consumes input from `InputAgent` and either:

* calls GitHub when a commit lookup is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTGitHubAgent
python agents/agent_GPTGitHubAgent/agent.py
```

### Scenario A — InputAgent, simple GitHub query as a string

In Terminal 2 (`InputAgent`), type:

```text
> Show me the last 3 commits for Summoner-Network/agent-sdk on GitHub.
```

`GPTGitHubAgent` should:

1. Receive this as `content` (a string).

2. Have GPT produce something like:

   ```json
   {"owner":"Summoner-Network","repo":"agent-sdk","max_commits":3}
   ```

3. Call the GitHub API with those arguments.

4. Return a payload similar to:

```log
[Received] {
  'tool': 'github',
  'performed_call': True,
  'result': {
    'owner': 'Summoner-Network',
    'repo': 'agent-sdk',
    'max_commits': 3,
    'count': 3,
    'commits': [
      {
        'sha': 'abcd1234...',
        'short_sha': 'abcd123',
        'author': 'First Author',
        'date': '2025-01-15T12:34:56Z',
        'subject': 'Short commit subject',
        'message': 'Full commit message...',
        'html_url': 'https://github.com/Summoner-Network/agent-sdk/commit/abcd1234...',
        'stats': { 'additions': 10, 'deletions': 2, 'total': 12 },
        'files': [
          {
            'filename': 'src/main.py',
            'additions': 5,
            'deletions': 1,
            'changes': 6
          }
        ]
      },
      ...
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'owner': 'Summoner-Network',
    'repo': 'agent-sdk',
    'max_commits': 3
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

### Scenario B — InputAgent, GitHub query as JSON

Here we vary the shape of the request to test the prompt's robustness. In Terminal 2, type:

```text
> {"owner":"Summoner-Network","repo":"agent-sdk","note":"if helpful, show me recent commits","n":5}
```

Even though the keys are slightly different (`note`, `n` instead of `max_commits`), GPT should interpret this as a GitHub commits request and produce arguments like:

```json
{"owner":"Summoner-Network","repo":"agent-sdk","max_commits":5}
```

You should again see a response where:

* `performed_call` is `True`,
* `result.commits` is a list of up to 5 commits,
* `tool_args.max_commits` reflects the inferred limit (`5`).

### Scenario C — InputAgent, no GitHub call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain what the Summoner-Network/agent-sdk project does conceptually."}
```

Here the user did not explicitly ask for recent commits or "what changed"; the intent is descriptive rather than "show latest changes". The `format_prompt` tells GPT to only request a GitHub call when a commits lookup clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call GitHub, and the response will look like:

```log
[Received] {
  'tool': 'github',
  'performed_call': False,
  'result': {
    'error': 'no_github_call_requested_or_missing_owner_repo',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these three scenarios to verify:

* that GitHub is called when the intent is clearly "show recent commits for this repo" (even with different field names or plain strings), and
* that no call is made when the request is purely explanatory.
