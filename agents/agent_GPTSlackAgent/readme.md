# `GPTSlackAgent`

A guarded GPT-powered agent that connects a **Slack workspace** to the Summoner network. It listens for `app_mention` events in allowed channels, decides whether to **relay** the request to backend agents, and then posts answers back into Slack threads using a second GPT pass.

Specifically, `GPTSlackAgent` listens for `app_mention` events via Slack **Socket Mode** and decides (with GPT) whether to:

* **Relay** the user’s request to backend agents on the Summoner server, and
* **Post** answers or status updates back into Slack threads,

using two separate GPT prompts:

* `relay_format_prompt` for **Slack → Summoner (relay)**,
* `post_format_prompt` for **Summoner → Slack (post)**.

It demonstrates how to:

* subclass `SummonerClient`,
* integrate Slack via **Socket Mode** (`SocketModeClient`) + **Web API** (`AsyncWebClient`),
* use **receive/send hooks** with async queues to bridge between Slack and the Summoner server,
* attach **handoff metadata** (channel, user, thread) for downstream agents,
* apply cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json) to structure Slack behavior,
* track a **dynamic channel allow/block policy**, learning from Slack errors (`not_in_channel`) and feeding this back into GPT with a spelling-based guard.

The agent uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with other agents (GitHub, Reddit, Notion, etc.) that answer the user’s question and send results back via the Summoner server.

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and its GPT adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/), extended with:
>
> * a Slack event loop (`SocketModeClient`) and posting loop (`AsyncWebClient.chat_postMessage`),
> * a **relay / post** split:
>
>   * `@agent.send(route="relay")` decides whether to send a Slack request into the Summoner network,
>   * `@agent.send(route="post")` decides how to summarize and post backend answers to Slack.

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
> **Slack app tokens required.**
> The agent uses **Socket Mode** and the Web API. It expects the following variables in your `.env`:
>
> ```env
> SLACK_BOT_TOKEN=xoxb-...
> SLACK_APP_TOKEN=xapp-...
> SLACK_SIGNING_SECRET=...
> ```
>
> * `SLACK_BOT_TOKEN` – **Bot User OAuth Token** for your app (starts with `xoxb-`), used for `chat.postMessage` and other Web API calls.  
> * `SLACK_APP_TOKEN` – **App-level token** (starts with `xapp-`), used by Socket Mode to open a WebSocket connection.  
> * `SLACK_SIGNING_SECRET` – the signing secret from **Basic Information → App Credentials**; not used directly by this agent yet, but useful if you later add HTTP endpoints.
>
> To obtain these values you must [create and install a Slack app](https://api.slack.com/apps/) in your workspace and enable Socket Mode. See [How to Run, step&nbsp;2](#slack-app-setup) (“Create your Slack app and get the tokens”) for a step-by-step procedure.
>
> If `SLACK_BOT_TOKEN` or `SLACK_APP_TOKEN` is missing, the agent logs:
>
> ```text
> SLACK_BOT_TOKEN or SLACK_APP_TOKEN missing; Slack integration disabled.
> ```
>
> and **does not** start the Slack Socket Mode client (the Summoner client still runs).


## Behavior

<details>
<summary><b>(Click to expand)</b> How the Slack agent processes mentions and backend answers</summary>
<br>

### 1. Startup and shared state

On startup, the `setup()` coroutine initializes three queues and some shared state:

* `to_server_buffer` – Slack → Summoner (normalized Slack events to be relayed).
* `from_server_buffer` – Summoner → Slack (backend answers / notifications).
* `to_slack_buffer` – final Slack posts ready to be sent via the Web API.
* `slack_ready` – an `asyncio.Event` set when the Socket Mode client is connected.
* `SEEN_SLACK_EVENTS` – a set of `(channel, ts)` keys to deduplicate Slack events.

Global Slack state:

* `SLACK_WEB_CLIENT` – `AsyncWebClient` built from `SLACK_BOT_TOKEN`.
* `SLACK_SOCKET_CLIENT` – `SocketModeClient` built from `SLACK_APP_TOKEN`.
* `NOT_ALLOWED_CHANNEL_IDS: set[str]` – channel IDs that Slack has proven unusable (e.g. `not_in_channel`, `channel_not_found`).
* `ALLOWED_CHANNEL_TOKENS: set[str]` – channel tokens (IDs or names) where posting has succeeded at least once.

These sets are used by the **channel guard** and exposed to GPT through `build_channel_policy_clause()`.

### 2. Agent initialization

`MyAgent(SummonerClient)`:

* Resolves `base_dir` to the agent folder.
* Loads environment (`load_dotenv()`).
* Validates `OPENAI_API_KEY` and builds an `AsyncOpenAI` client.
* Loads GPT config from `gpt_config.json` (or `--gpt <path>`), including:

  * `model`, `output_parsing`,
  * `max_chat_input_tokens`, `max_chat_output_tokens`,
  * `personality_prompt`,
  * `relay_format_prompt` (Slack → server),
  * `post_format_prompt` (server → Slack),
  * `sleep_seconds`, `cost_limit_usd`, `debug`.
* Loads an identity UUID `my_id` from `id.json` (or `--id <path>`).
* Optionally performs a model ID sanity check via `openai.models.list()`.

The `gpt_config.json` also encodes **Slack-safe formatting rules** for the `"text"` field (use `*bold*`, `_italic_`, `~strikethrough~`, backticks, and `<url|label>` instead of markdown `[label](url)`).

### 3. Hooks: receive and send

**Receive hook** (`@agent.hook(Direction.RECEIVE)`):

* Drops strings starting with `"Warning:"` after logging them as `[From Server]`.
* Ensures a message has `"remote_addr"` and `"content"`; otherwise logs:

  ```text
  [hook:recv] missing address/content
  ```

  and drops it.
* Otherwise logs:

  ```text
  [hook:recv] <addr> passed validation
  ```

  and forwards to route handlers.

**Send hook** (`@agent.hook(Direction.SEND)`):

* Logs:

  ```text
  [hook:send] sign <uuid>
  ```

* Wraps a raw string into `{"message": ...}`.

* Adds `{"from": my_id}` and returns the updated dict.

### 4. Slack event handling (`slack_handle_events`)

`slack_handle_events(sm_client, req)` is called by `SocketModeClient`:

1. **ACK** the envelope so Slack does not retry:

   ```python
   await sm_client.send_socket_mode_response(
       SocketModeResponse(envelope_id=req.envelope_id)
   )
   ```

2. Ignore non-`events_api` messages.

3. For `events_api`, extract the `event` payload and only handle:

   * `event["type"] == "app_mention"`,
   * `event.get("bot_id")` is **not** set (skip bot messages).

4. Extract fields:

   * `text` – full message text (including `@SummonerBot` mention).
   * `channel` – Slack channel ID.
   * `user` – user ID.
   * `ts` – message timestamp.
   * `thread_ts` – if present, otherwise fall back to `ts`.

5. **Hard blocking**:

   * If `channel in NOT_ALLOWED_CHANNEL_IDS`, log:

     ```text
     [slack] ignoring app_mention from NOT_ALLOWED channel=<channel>
     ```

     and return.

6. **Deduplication**:

   * Use `(channel, ts)` as a key. If already seen, log:

     ```text
     [slack] duplicate app_mention channel=<channel> ts=<ts>; ignoring
     ```

     and return.

7. Build a normalized payload and enqueue it into `to_server_buffer`:

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

   Log:

   ```text
   [slack] buffered app_mention from user=<user> channel=<channel>
   ```

This payload is what `send_relay` sees as `slack_event`.

### 5. Slack socket loop (`slack_socket_loop`)

`slack_socket_loop()`:

* Reads `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` from `.env`.
* If either is missing, logs a warning and returns (Slack integration disabled).
* Otherwise:

  * Builds `SLACK_WEB_CLIENT = AsyncWebClient(token=bot_token)`.

  * Builds `SLACK_SOCKET_CLIENT = SocketModeClient(app_token=app_token, web_client=SLACK_WEB_CLIENT)`.

  * Registers `slack_handle_events` as a listener.

  * Connects to Slack, logs:

    ```text
    Slack Socket Mode client connected.
    ```

  * Sets `slack_ready` so `slack_post_loop` knows it can start.

  * Waits forever on an `asyncio.Event` until cancelled.
* On cancellation, closes Slack clients and logs cleanup.

### 6. Slack posting loop (`slack_post_loop`)

`slack_post_loop()`:

* Waits for `slack_ready` and a non-`None` `SLACK_WEB_CLIENT`.
* Then repeatedly:

  1. Takes a payload from `to_slack_buffer`:

     ```python
     {
         "channel": raw_channel,
         "text": text,
         "thread_ts": thread_ts_or_None
     }
     ```

  2. Calls `normalize_post_channel(raw_channel)`:

     * Strips a leading `#` if present.
     * Rejects channels already in `NOT_ALLOWED_CHANNEL_IDS` (returns `None`).
     * Otherwise returns the token to send to Slack.

     If `normalize_post_channel(...)` returns `None`, logs:

     ```text
     [slack_post_loop] suppressed post to disallowed/unknown channel='<raw_channel>'
     ```

     and skips.

  3. Calls `chat_postMessage`:

     ```python
     kwargs = {"channel": channel, "text": text}
     if thread_ts:
         kwargs["thread_ts"] = thread_ts
     await SLACK_WEB_CLIENT.chat_postMessage(**kwargs)
     ```

     On success:

     ```text
     [slack_post_loop] posted to channel=<channel>
     ```

     and adds the `channel` token to `ALLOWED_CHANNEL_TOKENS`.

  4. On failure, logs a warning. If the error is a `SlackApiError` with `error` in `{"not_in_channel", "channel_not_found"}`:

     * Adds the channel to `NOT_ALLOWED_CHANNEL_IDS`.
     * Removes it from `ALLOWED_CHANNEL_TOKENS`.
     * Logs that future posts/events for this channel will be suppressed.

This is the runtime “learning” of blocked channels used by the GPT prompt.

### 7. Channel guard prompt (`build_channel_policy_clause`)

`build_channel_policy_clause()` generates a text snippet injected into `post_format_prompt` that describes:

* `<blocked_channels>` – each blocked channel spelled character-by-character, for example:

  ```text
  - random: r, a, n, d, o, m
  - summoner-tests: s, u, m, m, o, n, e, r, -, t, e, s, t, s
  ```
* `<allowed_channels>` – channels that have successfully accepted posts at least once.

It also encodes strict rules:

* Only **exact**, character-by-character matches to blocked entries are treated as blocked.
* Similarity or partial matches (e.g. `summoner-tests` vs `summonerbot-tests`) **do not** justify blocking.
* The model must:

  * explain the decision in a `reason` field, spelling the content’s channel character by character, and
  * then set `should_post` **based** on that reason.

This spelling-based redundancy stabilizes behavior with smaller models (e.g. `gpt-4o-mini`).

### 8. Server → Slack (`route="post"`)

**Receive** (`@agent.receive(route="post")`):

* Receives messages from the Summoner server.
* Buffers `msg["content"]` into `from_server_buffer`.
* Logs:

  ```text
  [recv:post] buffered content from SocketAddress=<addr>
  ```

**Send** (`@agent.send(route="post")`):

1. If `from_server_buffer` is empty, sleeps `sleep_seconds` and returns `None`.

2. Otherwise, takes one `content` item (non-blocking).

3. Extracts `handoff` (if any):

   ```python
   handoff = content.pop("handoff", {}) if isinstance(content, dict) else {}
   ```

   For Slack, a typical handoff from a backend agent looks like:

   ```json
   "handoff": {
     "GPTSlackAgent": {
       "channel": "C0A2TK7P8SC",
       "original_user_request": "@SummonerBot ...",
       "original_user": "U08M998MRME",
       "thread_ts": "1765141260.086369"
     }
   }
   ```

4. Scans for explicit target channels:

   * From `handoff[agent.name]["channel"]`,
   * From `content["slack_channel"]`,
   * From `content["slack"]["channel"]`.

   If **any** of these are in `NOT_ALLOWED_CHANNEL_IDS`, it logs:

   ```text
   [send:post] skipping GPT call for NOT_ALLOWED channels={'...'}
   ```

   sleeps, and returns `None`.

5. Builds a `prompt_payload` by stripping routing keys (`"to"`, `"from"`) and:

   * injecting `original_user_request` from handoff if present,
   * injecting `slack_channel` if handoff includes a channel.

6. Composes the GPT prompt:

   ```text
   <personality_prompt>
   <post_format_prompt>

   <channel_policy_clause>

   Content:
   <JSON-serialized prompt_payload>
   ```

7. Calls `gpt_call_async(...)` with `output_parsing="json"` and interprets the output as:

   ```json
   {
     "should_post": true|false,
     "channel": "#summonerbot-tests",
     "text": "Slack-safe answer...",
     "thread_ts": "1765141260.086369",
     "reason": "..."   // optional when should_post=false
   }
   ```

   Strings are parsed via `json.loads` if necessary; invalid outputs fall back to `{}`.

8. If `should_post` is true and both `channel` and `text` are present:

   * A final safety check uses `normalize_post_channel` to discard blocked channels even if GPT misbehaves.
   * If `user_id` is known from `handoff` and we are posting **outside** a thread, the agent prefixes the message with `<@user_id>` to ping the user.
   * The shaped payload is enqueued into `to_slack_buffer`.

9. Logs:

   ```text
   [send:post] model=<model> id=<uuid> cost=<cost>
   ```

   and sleeps `sleep_seconds`.

### 9. Slack → server (`route="relay"`)

`@agent.send(route="relay")` handles Slack events previously buffered by `slack_handle_events`:

1. If `to_server_buffer` is empty, sleeps `sleep_seconds` and returns `None`.

2. Otherwise, takes one `slack_event` (non-blocking) and logs it with `aprint`.

3. Extracts:

   ```python
   event_channel   = slack_event.get("channel")
   event_text      = slack_event.get("text")
   event_user      = slack_event.get("user")
   event_thread_ts = slack_event.get("thread_ts") or slack_event.get("ts")
   ```

4. Composes the relay prompt:

   ```text
   <personality_prompt>
   <relay_format_prompt>

   Content:
   <JSON-serialized slack_event>
   ```

5. Calls `gpt_call_async(...)` and expects:

   ```json
   {
     "relay": true|false,
     "server_payload": {...} | null,
     "slack_reply": {
       "post": true|false,
       "channel": "<channel_id>",
       "text": "Short acknowledgement or direct answer...",
       "thread_ts": "<thread_ts>"
     }
   }
   ```

6. If `slack_reply.post` is true and `channel`/`text` exist, enqueues the reply into `to_slack_buffer` (usually in the same thread).

7. Logs:

   ```text
   [send:relay] model=<model> id=<uuid> cost=<cost> relay=<True|False>
   ```

8. If `relay` is true and `server_payload` is a non-empty dict and `event_channel` is set, the agent attaches a `handoff` block:

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

   and returns `server_payload` to the server.

9. If `relay` is false or `server_payload` is invalid, returns `None`.

The result is:

* Simple queries or small talk are handled directly in Slack (no relay).
* Complex or external-data requests are forwarded to backend agents, with all necessary Slack context attached for the return path.

</details>


## SDK Features Used

| Feature                              | Description                                                                                                            |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`      | Subclasses `SummonerClient` to load configs, identity, and manage Slack/GPT state                                      |
| `@agent.hook(Direction.RECEIVE)`     | Validates or drops incoming messages from the server before routing                                                    |
| `@agent.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID                                                             |
| `@agent.receive(route="post")`       | Buffers server → Slack payloads into `from_server_buffer`                                                              |
| `@agent.send(route="post")`          | Uses GPT + channel guard to decide whether/how to post backend answers to Slack, enqueuing them into `to_slack_buffer` |
| `@agent.send(route="relay")`         | Uses GPT to decide whether to relay Slack messages to backend agents and how to acknowledge them in Slack              |
| `agent.loop.run_until_complete(...)` | Runs `setup()` and coordinates the Slack background tasks                                                              |
| `agent.run(...)`                     | Connects to the Summoner server and starts the asyncio event loop                                                      |
| `agent.logger`                       | Logs Slack events, buffering, GPT decisions, posting failures/successes, and channel allow/block learning              |


## How to Run

1. Start the Summoner server:

```bash
python server.py
# or, for cleaner logs:
python server.py --config configs/server_config_nojsonlogs.json
```

<a id="slack-app-setup"></a>

2. Create your Slack app and get the tokens:

    <details><summary><em>(Click to expand) Step-by-step procedure</em></summary>

    1. Go to the Slack API dashboard:
    [https://api.slack.com/apps/](https://api.slack.com/apps/)

    2. Click **“Create New App”** → **“From scratch”**:

        * Give it a name, for example: `SummonerBot`.
        * Select the workspace where you want to install it.

    3. Get the **Signing Secret**:

        * In the left sidebar, click **Basic Information**.
        * Under **App Credentials**, copy the **Signing Secret**.
        * Store it in your `.env`:

            ```env
            SLACK_SIGNING_SECRET=your_signing_secret_here
            ```

    4. Create the **Bot User OAuth Token** (`SLACK_BOT_TOKEN`):

        * In the left sidebar, go to **OAuth & Permissions**.
        * Under **Scopes → Bot Token Scopes**, add at least:

            * `app_mentions:read`
            * `channels:read`
            * `channels:history`
            * `chat:write`
            (add DM/private scopes if you need them, like `groups:read`, `groups:history`, `im:history`, `im:write`).
        * Click **Install App to Workspace** (or **Reinstall to Workspace**).
        * After installing, copy the **Bot User OAuth Token** (starts with `xoxb-`) and set:

            ```env
            SLACK_BOT_TOKEN=xoxb-your-bot-token
            ```

    5. Create the **App-level token** (`SLACK_APP_TOKEN`) for Socket Mode:

        * Go back to **Basic Information**.
        * Scroll to **App-Level Tokens** and click **“Generate Token and Scopes”**.
        * Give it a name (for example `socket-mode`) and add the scope:

            * `connections:write`
        * Save and copy the new token (starts with `xapp-`) into `.env`:

            ```env
            SLACK_APP_TOKEN=xapp-your-app-level-token
            ```

    6. Enable **Socket Mode** and subscribe to events:

        * In the left sidebar, go to **Socket Mode** and toggle **Enable Socket Mode**.
        * Then go to **Event Subscriptions**:

            * Enable **Events**.
            * Under **Subscribe to bot events**, add:

            * `app_mention`
            * Save changes and reinstall the app if Slack asks.

    7. Invite the bot to your test channel(s):

        * In Slack, go to a channel such as `#summonerbot-tests`.
        * Use `/invite @SummonerBot` to add the app to that channel.

    </details>
    <br>

3. Make sure your `.env` at the project root includes:

```env
OPENAI_API_KEY=sk-...your_key...
SLACK_BOT_TOKEN=xoxb-...your_bot_token...
SLACK_APP_TOKEN=xapp-...your_app_level_token...
SLACK_SIGNING_SECRET=your_signing_secret_here
```

4. Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTSlackAgent/`:

* `gpt_config.json` – includes:

  * `model`, `sleep_seconds`, `output_parsing`, token/cost limits,
  * `personality_prompt` with Slack formatting rules,
  * `relay_format_prompt` and `post_format_prompt` as in your current config.
* `id.json` – at minimum:

  ```json
  {"uuid": "some-unique-uuid-for-slack-agent"}
  ```

5. Start the Slack agent:

```bash
python agents/agent_GPTSlackAgent/agent.py
# or with explicit client config:
python agents/agent_GPTSlackAgent/agent.py --config configs/client_config.json
```

The agent:

* Connects to the Summoner server,
* Starts Slack Socket Mode and posting loops (if tokens are present),
* Begins listening for `@SummonerBot` mentions and `post` messages from backend agents.


## Simulation Scenarios

These scenarios show how `GPTSlackAgent` coordinates Slack mentions, the Summoner server, and backend agents (e.g. GitHub, InputAgent).

<p align="center">
  <img width="750px" src="../../assets/img/slack_summoner_rounded.png" />
</p>


### Scenario A — Simple Slack mention, no backend

```bash
# Terminal 1: Summoner server
python server.py

# Terminal 2: GPTSlackAgent
python agents/agent_GPTSlackAgent/agent.py
```

<p align="center">
  <img width="350px" src="../../assets/img/slack_summoner_default_rounded.png" />
</p>


Assuming your Slack app is named `SummonerBot`, in Slack type:

```text
@SummonerBot How are you?
```

`GPTSlackAgent` receives the event from Slack as (example):

```python
{
  'source': 'slack',
  'event_type': 'app_mention',
  'channel': '<your_slack_channel_id>',
  'user': '<your_user_id>',
  'ts': '1765140822.115419',
  'thread_ts': '1765140822.115419',
  'text': '<@app_id> How are you?'
}
```

It then:

1. Runs this payload through the **relay** prompt (`relay_format_prompt`).
2. Decides that this is a simple greeting that does **not** need backend processing (`relay = false`).
3. Produces a short Slack reply (via `slack_reply`) and posts it as a **threaded reply** under your message.

In Slack you should see something like:

```text
I'm here to assist with any questions or requests you might have. If you need more complex help, I can route that to backend agents.
```

`GPTSlackAgent` always tries to keep the original thread active. In cases where it cannot answer fully (for example, a malformed relay decision), it will fall back to explicitly mentioning you (e.g. `@your_user`) to indicate that something went wrong.


### Scenario B — Backend-initiated post into an allowed channel

```bash
# Terminal 1: Summoner server
python server.py

# Terminal 2: GPTSlackAgent
python agents/agent_GPTSlackAgent/agent.py

# Terminal 3: some backend agent (e.g. InputAgent controlling others)
python agents/agent_InputAgent/agent.py
```

<p align="center">
  <img width="550px" src="../../assets/img/slack_summoner_input_rounded.png" />
</p>


In Terminal 3, after `InputAgent` connects to the server, send a request targeted at the Slack agent:

```log
2025-12-07 15:43:41.154 - InputAgent - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Say Hello to the users in the channel #summonerbot-tests
```

The backend message reaches `GPTSlackAgent` on the `post` route. After passing through the **post** prompt and channel guard, the model might produce:

```json
{"should_post": true, "channel": "#summonerbot-tests", "text": "Hello everyone in the #summonerbot-tests channel!"}
```

If your Slack app is allowed to post in `#summonerbot-tests`, the `slack_post_loop` uses this decision to call `chat.postMessage`. In the Slack channel you should see:

```text
Hello everyone in the #summonerbot-tests channel!
```

On the first successful post, the channel token (here `summonerbot-tests`) is added to `ALLOWED_CHANNEL_TOKENS`, so future posts can preferentially target it.


### Scenario C — Channel is not allowed (`not_in_channel`) and becomes blocked

```bash
# Terminal 1: Summoner server
python server.py

# Terminal 2: GPTSlackAgent
python agents/agent_GPTSlackAgent/agent.py

# Terminal 3: some backend agent (e.g. InputAgent)
python agents/agent_InputAgent/agent.py
```

<p align="center">
  <img width="550px" src="../../assets/img/slack_summoner_input_rounded.png" />
</p>

Suppose you ask the Slack agent to greet users in a channel where the bot is **not** a member:

```log
2025-12-07 15:43:41.154 - InputAgent - INFO - Connected to server @(host=127.0.0.1, port=8888)
> Say Hello to the users in the channel #random
```

The Slack agent passes this through the post-prompt and the channel guard, and the model decides:

```json
{"reason": "#random does not match any blocked channel exactly.", "should_post": true, "channel": "#random", "text": "Hello everyone in the #random channel!"}
```

The **decision** itself is acceptable: `#random` is not yet known to be blocked, so `should_post = true`.

However, when the agent actually calls Slack, the Web API responds:

```text
2025-12-07 20:01:55.418 - GPTSlackAgent - WARNING - [slack_post_loop] failed to post: SlackApiError: The request to the Slack API failed. (url: https://slack.com/api/chat.postMessage, status: 200)
The server responded with: {'ok': False, 'error': 'not_in_channel'}
2025-12-07 20:01:55.418 - GPTSlackAgent - INFO - [slack_post_loop] learned not_in_channel for channel_id=random; future posts and events for this channel will be suppressed.
```

This has two important consequences:

1. The channel ID `random` is added to `NOT_ALLOWED_CHANNEL_IDS`.
2. The next time you try:

   ```log
   > Say Hello to the users in the channel #random
   ```

   the GPT decision (with the updated `channel_policy_clause`) will typically be:

   ```json
   {"reason": "The channel matches the blocked name 'random' exactly (r, a, n, d, o, m).", "should_post": false}
   ```

The prompt explicitly spells out blocked channel names character-by-character to avoid **false blocking** of similar names when using `gpt-4o-mini`.

For example, after the previous failure, the prompt might contain:

```text
Blocked Slack channels. This is a sensitive field: channel names with slight variation must NOT be blocked by mistake.
- random: r, a, n, d, o, m
- summoner-tests: s, u, m, m, o, n, e, r, -, t, e, s, t, s
You must treat a channel as blocked only when the Content channel token matches one of these entries EXACTLY, character by character. Similarity or partial matches do not count.
```

Now, if you ask:

```log
> Say Hello to the users in the channel #summonerbot-tests
```

the model sees that:

* `summonerbot-tests` is **not** identical to `summoner-tests`, and
* `summonerbot-tests` is not in the blocked list,

so the guard allows `should_post = true`. Assuming the app is allowed in `#summonerbot-tests`, you would see in that channel:

```text
Hello everyone in the #summonerbot-tests channel!
```

This spelling-based guard is the key piece that prevents an over-eager block of channels with similar names.


### Scenario D — Slack → GitHub via Summoner, then back to Slack

```bash
# Terminal 1: Summoner server
python server.py

# Terminal 2: GPTSlackAgent
python agents/agent_GPTSlackAgent/agent.py

# Terminal 3: some backend agent (e.g. GPTGitHubAgent)
python agents/agent_GPTGitHubAgent/agent.py
```

<p align="center">
  <img width="580px" src="../../assets/img/slack_summoner_github_rounded.png" />
</p>

In Slack, ask a question that needs a backend GitHub query:

```text
@SummonerBot What is the hash of the last commit in the summoner-sdk repo in the account Summoner-Network (GitHub)?
```

`GPTSlackAgent` normalizes the event as:

```python
{
  'source': 'slack',
  'event_type': 'app_mention',
  'channel': 'C0A2TK7P8SC',
  'user': 'U08M998MRME',
  'ts': '1765141260.086369',
  'thread_ts': '1765141260.086369',
  'text': '<@U096DQRBD98> What is the hash of the last commit in the `summoner-sdk` repo in the account `Summoner-Network` (github)?'
}
```

The relay prompt recognizes this as a request that should be handled by a backend agent:

1. `relay = true`

2. `server_payload` contains something like:

   ```json
   {
     "intent": "github_last_commit_hash",
     "query": "What is the hash of the last commit in the summoner-sdk repo in the account Summoner-Network?",
     "slack": {
       "channel": "C0A2TK7P8SC",
       "user": "U08M998MRME",
       "ts": "1765141260.086369",
       "event_type": "app_mention"
     }
   }
   ```

3. `slack_reply` is a short acknowledgement, for example:

   ```text
   I've forwarded your request to backend agents. I'll respond here when I have an answer.
   ```

This acknowledgement is posted immediately in the same thread.

The `server_payload` is then delivered to the Summoner server, which routes it to `GPTGitHubAgent`. That agent calls the GitHub API, computes the last commit, and sends the result back to the server, which forwards it to `GPTSlackAgent` via `route="post"`.

`GPTSlackAgent` then:

* receives the GitHub answer as `content` on the `post` route, including the original thread metadata in `handoff`,
* passes it through the **post** prompt,
* decides that it **should** post the answer in Slack, and shapes a decision such as:

```json
{
  "should_post": true,
  "channel": "#summonerbot-tests",
  "text": "The last commit in the `summoner-sdk` repository by `Summoner-Network` has the hash `07ec963` with the subject \"updated installation procedure\". You can view it here: https://github.com/Summoner-Network/summoner-sdk/commit/07ec9637890a5ead615f682f8e396a26e3a03f48",
  "thread_ts": "1765141260.086369"
}
```

> [!NOTE]
> for Slack compatibility, the `text` field uses either a plain URL (as above) or the `<url|label>` form; markdown `[label](url)` is avoided per the `gpt_config` instructions.

In Slack, in the **same thread** where you asked the original question, you should then see something like:

```text
The last commit in the `summoner-sdk` repository by `Summoner-Network` has the hash `07ec963` with the subject "updated installation procedure". You can view it here: https://github.com/Summoner-Network/summoner-sdk/commit/07ec9637890a5ead615f682f8e396a26e3a03f48
```

This scenario shows the full round-trip:

1. User asks a question in Slack.
2. `GPTSlackAgent` relays to Summoner and acknowledges.
3. A backend agent (here GitHub) performs the actual work.
4. `GPTSlackAgent` decides how to present the result back in Slack, with both channel guard and Slack-native formatting applied.
