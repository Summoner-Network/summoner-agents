# `GPTSlackAgent`

A guarded GPT-powered agent that connects a **Slack workspace** to the Summoner network. It listens for `app_mention` events in allowed channels, decides whether to **relay** the request to backend agents, and then posts answers back into Slack threads using a second GPT pass.

It demonstrates how to:

* subclass `SummonerClient`,
* integrate Slack via **Socket Mode** + `AsyncWebClient`,
* use **receive/send hooks** with async queues to bridge between Slack and the Summoner server,
* attach **handoff metadata** (channel, user, thread) for downstream agents,
* apply cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json) to:

  * decide whether to relay a Slack message (`relay_format_prompt`),
  * shape replies sent back to Slack (`post_format_prompt`),
* enforce a **channel allowlist** and learn a runtime blocklist when Slack returns `not_in_channel`.

The agent uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with other GPT-based agents (e.g. GitHub, Reddit, Notion) that answer the user’s question and send results back via the Summoner server.

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and its GPT adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/), extended with:
>
> * a Slack event loop (`SocketModeClient`) and posting loop (`AsyncWebClient.chat_postMessage`),
> * a **relay / post** split, where:
>
>   * `@agent.send(route="relay")` decides whether to send the request to the server,
>   * `@agent.send(route="post")` decides how to summarize and post backend answers to Slack.

> [!IMPORTANT]
> **OpenAI and Slack credentials required.**
>
> The agent calls `load_dotenv()` and expects:
>
> * `OPENAI_API_KEY` – for GPT calls,
> * `SLACK_BOT_TOKEN` – Slack bot token,
> * `SLACK_APP_TOKEN` – Slack app-level token for Socket Mode.
>
> Put a `.env` file at the **project root** (or set the variables in your shell/CI):
>
> * **.env:**
>
> ```env
> OPENAI_API_KEY=sk-...your_key...
> SLACK_BOT_TOKEN=xoxb-...your_bot_token...
> SLACK_APP_TOKEN=xapp-...your_app_level_token...
> ```
>
> If `OPENAI_API_KEY` is missing, the agent raises `RuntimeError("OPENAI_API_KEY missing in environment.")`.
>
> If `SLACK_BOT_TOKEN` or `SLACK_APP_TOKEN` is missing, the agent logs:
>
> ```text
> SLACK_BOT_TOKEN or SLACK_APP_TOKEN missing; Slack integration disabled.
> ```
>
> and does **not** start the Slack Socket Mode client (the Summoner client still runs).

## Behavior

<details>
<summary><b>(Click to expand)</b> How the Slack agent processes mentions and replies:</summary>
<br>

1. On startup, the `setup` coroutine initializes three `asyncio.Queue` objects:

   * `to_server_buffer` – Slack → server (relay user requests),
   * `from_server_buffer` – server → Slack (backend answers),
   * `to_slack_buffer` – GPT-shaped Slack posts ready to send.

   It also sets up:

   * `slack_ready` – an `asyncio.Event` that signals when the Slack client is connected,
   * `SEEN_SLACK_EVENTS` – a set of `(channel, ts)` pairs for deduplication.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * **OpenAI** API key from `OPENAI_API_KEY`,

   * **GPT config** from `gpt_config.json` (or `--gpt <path>`), including:

     * `model`, `output_parsing`, `max_chat_input_tokens`, `max_chat_output_tokens`,
     * `personality_prompt`, `relay_format_prompt`, `post_format_prompt`,
     * `sleep_seconds`, `cost_limit_usd`, `debug`,

   * An identity UUID (`my_id`) from `id.json` (or `--id <path>`),

   * A small **Slack channel allowlist**:

     ```python
     ALLOWED_CHANNEL_IDS = {
         "C07AA1K6BQB": "#general",
         "C08K6V1SVB2": "#gm-ga-ge-gn",
         "C0A2TK7P8SC": "#summonerbot-tests",
         "#general": "C07AA1K6BQB",
         "#gm-ga-ge-gn": "C08K6V1SVB2",
         "#summonerbot-tests": "C0A2TK7P8SC",
     }
     ```

     plus runtime sets:

     * `BLOCKED_CHANNEL_IDS`, `BLOCKED_CHANNEL_NAMES` – channels that returned `not_in_channel`,
     * `NOT_ALLOWED_CHANNEL_IDS` – treated as hard-blocked for both events and posts.

3. **Receive hook** (`@agent.hook(Direction.RECEIVE)`):

   * Drops strings starting with `"Warning:"` after logging them as `[From Server]`.

   * Drops any message not shaped as `{"remote_addr": ..., "content": ...}` and logs:

     ```text
     [hook:recv] missing address/content
     ```

   * Otherwise logs:

     ```text
     [hook:recv] <addr> passed validation
     ```

     and forwards to route handlers.

4. **Send hook** (`@agent.hook(Direction.SEND)`):

   * Logs:

     ```text
     [hook:send] sign <uuid>
     ```

   * Wraps raw strings into `{"message": ...}`,

   * Adds a `{"from": my_id}` field,

   * Returns the updated dict for sending back to the server.

5. **Slack event handling** (`slack_handle_events`):

   The Slack agent uses `SocketModeClient` to receive events and a dedicated handler:

   ```python
   async def slack_handle_events(sm_client: SocketModeClient, req: SocketModeRequest):
       ...
   ```

   Steps:

   1. ACK the envelope so Slack does not retry:

      ```python
      await sm_client.send_socket_mode_response(
          SocketModeResponse(envelope_id=req.envelope_id)
      )
      ```

   2. Only process `events_api` envelopes, and inside them only `app_mention` events:

      * Ignore non-`events_api` messages,
      * Ignore events where `event["type"] != "app_mention"` or `event.get("bot_id")` is set (bot messages).

   3. Extract:

      * `text` – full message including `@SummonerBot` mention,
      * `channel` – Slack channel ID,
      * `user` – user ID,
      * `ts` – message timestamp,
      * `thread_ts` – if present, otherwise falls back to `ts`.

   4. Enforce channel policy:

      * If `channel` in `NOT_ALLOWED_CHANNEL_IDS`, ignore and log:

        ```text
        [slack] ignoring app_mention from NOT_ALLOWED channel=<id>
        ```

      * If `ALLOWED_CHANNEL_IDS` is non-empty and `channel` not in this mapping, ignore and log:

        ```text
        [slack] ignoring app_mention from disallowed channel=<id>
        ```

   5. Deduplicate `(channel, ts)` so repeated deliveries do not double-trigger:

      ```text
      [slack] duplicate app_mention channel=<id> ts=<ts>; ignoring
      ```

   6. Build a normalized payload and enqueue it in `to_server_buffer`:

      ```python
      payload = {
          "source": "slack",
          "event_type": "app_mention",
          "channel": channel,
          "user": user,
          "ts": ts,
          "thread_ts": thread_ts,
          "text": text,
      }
      ```

      and log:

      ```text
      [slack] buffered app_mention from user=<user> channel=<channel>
      ```

6. **Slack socket loop** (`slack_socket_loop`):

   * Builds `AsyncWebClient` and `SocketModeClient`,
   * Registers `slack_handle_events`,
   * Connects to Slack and sets `slack_ready`,
   * Keeps running until cancelled, then performs best-effort cleanup.

7. **Slack posting loop** (`slack_post_loop`):

   * Waits for `slack_ready`,
   * Continuously:

     1. Reads `{"channel": raw_channel, "text": text, "thread_ts": thread_ts}` from `to_slack_buffer`,

     2. Normalizes the channel with `normalize_post_channel`:

        * Accepts canonical `#name` or known IDs,
        * Rejects anything not in the allowlist or learned blocklist,

     3. Uses `chat_postMessage` to send:

        ```python
        kwargs = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await SLACK_WEB_CLIENT.chat_postMessage(**kwargs)
        ```

     4. Logs success or failure,

     5. If Slack returns `not_in_channel`, it updates `BLOCKED_CHANNEL_IDS` and `NOT_ALLOWED_CHANNEL_IDS` and logs that future events/posts for that channel will be suppressed.

8. **Server → Slack path** (`@agent.receive(route="post")` and `@agent.send(route="post")`):

   * `recv_post`:

     * Receives messages from the server with potential Slack content,
     * Enqueues `msg["content"]` into `from_server_buffer`,
     * Logs:

       ```text
       [recv:post] buffered content from SocketAddress=<addr>
       ```

   * `send_post`:

     1. Non-blocking poll from `from_server_buffer`,

     2. Extracts any `handoff` metadata:

        ```python
        handoff = content.pop("handoff", {})
        ```

        For this Slack agent, a typical handoff from a backend agent looks like:

        ```json
        "handoff": {
          "GPTSlackAgent": {
            "channel": "C07AA1K6BQB",
            "original_user_request": "@SummonerBot What are the latest updates in our repo ...?",
            "original_user": "U123456",
            "thread_ts": "1733412345.6789"
          }
        }
        ```

     3. Builds a `prompt_payload` by stripping routing keys (`"to"`, `"from"`) and, if present:

        * Adds `original_user_request` as a strong hint:

          ```python
          prompt_payload["original_user_request"] = (
              "You MUST respond to this user's message: " + user_request
          )
          ```

        * For `slack_channel`, converts channel ID → canonical `#name` when possible so the model can reason about channels.

     4. Composes the GPT prompt:

        ```text
        <personality_prompt>
        <post_format_prompt>

        <channel_policy_clause>

        Content:
        <JSON-serialized prompt_payload>
        ```

        where `channel_policy_clause` describes allowed / blocked channels.

     5. Calls `gpt_call_async(...)` with `output_parsing="json"` and expects a decision of the form:

        ```json
        {
          "should_post": true,
          "channel": "#gm-ga-ge-gn",
          "text": "…Slack-safe text…",
          "thread_ts": "1733412345.6789"
        }
        ```

        or `{ "should_post": false, "reason": "…" }`.

     6. If `should_post` is `true` and both `channel` and `text` are present:

        * Applies a final safety check via `normalize_post_channel`,

        * If `user_id` is known **and** we’re **not** posting into a thread, prefixes `<@user_id>` to the text to ping the original user:

          ```python
          text = f"<@{user_id}> " + text
          ```

        * Enqueues the message into `to_slack_buffer`.

9. **Slack → server path** (`@agent.send(route="relay")`):

   * Non-blocking poll from `to_server_buffer` to retrieve a Slack event.

   * Logs the raw event with `aprint`.

   * Extracts canonical fields:

     ```python
     event_channel   = slack_event.get("channel")
     event_text      = slack_event.get("text")
     event_user      = slack_event.get("user")
     event_thread_ts = slack_event.get("thread_ts") or slack_event.get("ts")
     ```

   * Composes the relay prompt:

     ```text
     <personality_prompt>
     <relay_format_prompt>

     Content:
     <JSON-serialized slack_event>
     ```

   * Calls `gpt_call_async(...)` and expects an output:

     ```json
     {
       "relay": true,
       "server_payload": {
         "intent": "slack_question",
         "query": "@SummonerBot What are the latest updates in our repo ...?",
         "slack": {
           "channel": "C07AA1K6BQB",
           "user": "U123456",
           "ts": "1733412345.6789",
           "event_type": "app_mention"
         }
       },
       "slack_reply": {
         "post": true,
         "channel": "C07AA1K6BQB",
         "text": "Got it — I’ve forwarded this to the backend agents and will reply here with the result.",
         "thread_ts": "1733412345.6789"
       }
     }
     ```

   * If `slack_reply.post` is true, enqueues an immediate Slack acknowledgement into `to_slack_buffer` (threaded reply).

   * Logs:

     ```text
     [send:relay] model=<model> id=<uuid> cost=<usd_or_none> relay=<True|False>
     ```

   * If `relay` is true and `server_payload` is a non-empty dict, attaches a **handoff** block so downstream agents can route the answer back to the right Slack thread:

     ```python
     server_payload["handoff"] = {
         agent.name: {
             "channel": event_channel,
             "original_user_request": event_text,
             "original_user": event_user,
             "thread_ts": event_thread_ts,
         }
     }
     ```

   * Returns `server_payload` to the server (or `None` if no relay).

10. The agent sleeps for `sleep_seconds` between send cycles and runs until stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                              | Description                                                                                               |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage Slack/GPT state                         |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling                                                 |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                                |
| `@agent.receive(route="post")`       | Buffers server → Slack payloads into `from_server_buffer`                                                 |
| `@agent.send(route="post")`          | Uses GPT to decide whether/how to post backend answers to Slack, enqueues messages into `to_slack_buffer` |
| `@agent.send(route="relay")`         | Uses GPT to decide whether to relay Slack messages to the server and how to acknowledge them in Slack     |
| `agent.logger`                       | Logs hook activity, Slack events, GPT decisions, and send lifecycle events                                |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize queues and shared state                                          |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                                  |

## How to Run

1. Start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

2. Create Slack app + tokens (once, in the Slack UI):

   * Create a Slack app (from “From scratch”) with:

     * **Socket Mode** enabled,
     * the app installed to your workspace,
   * Generate:

     * `SLACK_APP_TOKEN` (app-level token, usually starts with `xapp-`),
     * `SLACK_BOT_TOKEN` (bot token, usually starts with `xoxb-`),
   * Add the bot to the channels you want to monitor:

     * e.g. `#general`, `#gm-ga-ge-gn`, `#summonerbot-tests`.

3. Prepare `.env` at the project root:

```env
OPENAI_API_KEY=sk-...your_key...
SLACK_BOT_TOKEN=xoxb-...your_bot_token...
SLACK_APP_TOKEN=xapp-...your_app_level_token...
```

4. Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTSlackAgent/`.

A **typical `gpt_config.json`** (you already have this, included here for context) defines:

* the model and guardrails,
* a `personality_prompt`,
* `relay_format_prompt` (Slack → server),
* `post_format_prompt` (server → Slack),

and includes explicit Slack formatting instructions.

5. Start the Slack agent:

```bash
python agents/agent_GPTSlackAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).

## Simulation Scenarios

These scenarios show how `GPTSlackAgent` coordinates Slack mentions, the Summoner server, and backend agents (e.g. GitHub).

Assume:

```bash
# Terminal 1: Summoner server
python server.py

# Terminal 2: GPTSlackAgent
python agents/agent_GPTSlackAgent/agent.py

# Terminal 3: some backend agent (e.g. GPTRedditAgent, GPTGitHubAgent)
python agents/agent_GPTGitHubAgent/agent.py
```

### Scenario A — User asks a technical question, Slack → GitHub → Slack

In Slack (in `#gm-ga-ge-gn`), a user types:

```text
@SummonerBot What are the latest updates in our repo summoner-agents in our account Summoner-Network?
```

Expected flow:

1. Slack sends an `app_mention` event to the app.

2. `GPTSlackAgent`:

   * receives it via `slack_handle_events`,
   * enqueues into `to_server_buffer`.

3. In `send_relay`:

   * GPT runs with `relay_format_prompt`,

   * decides `relay = true`,

   * returns something like:

     ```json
     "server_payload": {
       "intent": "slack_question",
       "query": "What are the latest updates in our repo summoner-agents in our account Summoner-Network?",
       "slack": {
         "channel": "C08K6V1SVB2",
         "user": "U123456",
         "ts": "1733412345.6789",
         "event_type": "app_mention"
       }
     },
     "slack_reply": {
       "post": true,
       "channel": "C08K6V1SVB2",
       "text": "Got it – I’ve forwarded this to the backend agents and will reply here once I have an answer.",
       "thread_ts": "1733412345.6789"
     }
     ```

   * The immediate acknowledgement is posted as a thread reply under the user’s message.

4. The server routes `server_payload` (with `handoff`) to `GPTGitHubAgent` which calls GitHub and returns:

   ```json
   {
     "tool": "github",
     "performed_call": true,
     "result": {
       "owner": "Summoner-Network",
       "repo": "summoner-agents",
       "max_commits": 5,
       "...": "..."
     },
     "tool_args": {...},
     "to": "GPTSlackAgent"
   }
   ```

5. When `GPTSlackAgent` receives this on its `"post"` route:

   * `recv_post` buffers the `content`,
   * `send_post`:

     * merges `handoff[ "GPTSlackAgent" ]` into the prompt payload (with `original_user_request`, `original_user`, `thread_ts`),
     * GPT decides `should_post = true` and chooses the Slack channel (by name, e.g. `"#gm-ga-ge-gn"`),
     * returns a Slack-compatible summary text.

6. The final answer is posted back in the same thread, optionally pinging the original user.

### Scenario B — User sends small talk, no relay

In Slack:

```text
@SummonerBot gm, how’s it going?
```

Here the relay prompt is designed to **not** relay pure greetings.

Expected behavior:

1. Slack event is buffered as usual.
2. `send_relay`:

   * GPT decides `relay = false`,
   * `server_payload = null`,
   * `slack_reply` contains a short, neutral greeting reply.
3. The agent posts the reply and **does not** send anything to the Summoner server (no backend work).

### Scenario C — Attempted post to a disallowed or blocked channel

If a backend agent mistakenly sets a channel not in `ALLOWED_CHANNEL_IDS` (or one that Slack responded to with `not_in_channel`):

1. GPT might output something like:

   ```json
   { "should_post": true, "channel": "#random", "text": "..." }
   ```

2. `send_post` will still enqueue it with the raw `channel` string.

3. In `slack_post_loop`, `normalize_post_channel("#random")` returns `None`, and the agent logs:

   ```text
   [slack_post_loop] suppressed post to disallowed/unknown channel='#random'
   ```

No message is sent to Slack, and the runtime blocklists prevent repeated attempts.

### Scenario D — Using threads naturally

Because the agent tracks `thread_ts`:

* A **top-level mention** creates a thread where:

  * The acknowledgment reply is posted with `thread_ts = original ts`,
  * Backend answers and follow-ups continue under the same `thread_ts`.
* If the user later replies inside that thread and mentions `@SummonerBot` again, the new `app_mention` carries the same `thread_ts`, so follow-up answers stay in the same conversation.

This makes interactions with @SummonerBot feel natural and organized in Slack.

