# `ExamAgent_1`

An exam-style agent based on [`ExamAgent_0`](../agent_ExamAgent_0/) that runs timed multiple-choice rounds over the Summoner protocol. It listens for a free-form message to start a round, then publishes questions, collects answers in a short window, scores, announces the winner, and renders a live scoreboard. Utilities live in [`exam_utils.py`](./exam_utils.py). Two sample test sets are included:

* [qa_foundations.json](./qa_foundations.json)
* [qa_sysdesign_gradual.json](./qa_sysdesign_gradual.json)

You can also provide your own JSON file in the same format.

> [!NOTE]
> **What's new vs. [`ExamAgent_0`](../agent_ExamAgent_0/):** this version uses the **flow engine** (routes + events) to orchestrate receivers, reducing the lifecycle to **two states**—`none` and `started`. It introduces:
>
> * `@client.receive(route="none --> started")` to **start** a round on any valid message.
> * `@client.receive(route="started")` to accept and score labels during a round.
> * `@client.upload_states()` / `@client.download_states()` to integrate route-driven transitions into `phase`.
> * A `@client.hook(Direction.RECEIVE)` that prints inbound text, **drops** `Warning:` messages, and forwards valid dict payloads to receivers.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the agent parses CLI flags and applies settings.

   * `--qa <path>` is required. The file is loaded by `Questions(source=..., limit=2)` so only the first two questions are used each round. Errors surface if the path is missing or JSON is invalid.
   * `--colors 0|1` controls ANSI styling through `Style.enable_colors(...)`. Set to 0 for plain text terminals or log collectors.
   * `--countdown 0|1` enables the local timer through `Countdown.configure(...)`. When on, a visible countdown appears during the answer window.
   * `--config <path>` sets the client config for `SummonerClient`. Default is `configs/client_config.json`.

2. The agent loads and validates the question set.

   * Each item must have a `question` string and an `answers` map from labels to `{val, pts}`.
   * `render_question(idx)` prints the title, labeled choices, and the hint to reply with a label.
   * `score_answer(content, idx)` compares only the label, case-insensitively.

3. The agent initializes flow state and activates the automaton.

   * `phase ∈ {none, started}` controls the lifecycle.
   * `question_tracker = None` until `Q#0` is sent.
   * `answer_buffer` and `variables_lock` are created in `setup()` (on the client's event loop).
   * `answer_buffer` stores `(addr, idx, pts, ts)` tuples; `variables_lock` guards shared updates across coroutines.
   * `score = ScoreKeeper()` tracks and renders totals.
   * The flow engine is enabled with `client.flow().activate()` and an arrow style is declared using `client.flow().add_arrow_style(...)`.
   * `@client.upload_states()` reports the current `phase`; `@client.download_states()` folds allowed next nodes back into `phase`.

4. The receive hook validates inbound payloads before routing.

   * Prints all visible content.
   * Drops messages whose text starts with `Warning:` after printing them.
   * Forwards valid dict payloads (with `"content"` and `"remote_addr"`) to receivers; raw strings are shown then dropped.

5. The receive routes trigger start and queue answers.

   * `route="none --> started"`: any valid message moves to `started`. `Q#0` will publish on the next send tick.
   * `route="started"`: accepts labels and enqueues `(addr, idx, pts, ts)` for the **active** question. If a label arrives before `Q#0` is published, it is ignored gracefully.

6. The send coroutine publishes questions, manages windows, and picks winners.

   * If `phase == none`, it sends nothing. If `phase == started` and `question_tracker is None`, it sets `question_tracker = 0` and sends `Q#0`.
      * Before publishing `Q#0`, the agent clears any stale answers from the buffer to prevent late messages from a previous window affecting the new one.

   * For each question:

      * It collects answers for 5 seconds beginning at the first answer. If countdown is enabled, the timer prints locally and clears on completion.
      * It selects a winner with deterministic rules: keep only the first submission per address, **only consider answers to the current index**, then break ties by higher points, then earlier time.
      * If the winner answered the current index, it updates the scoreboard and prints a result line. Otherwise it notes that no one answered that index.
      * It advances `question_tracker` modulo the loaded set. After two questions, it prints a reset notice, clears scores, flips `phase = none`, and waits for the next message.
      * On round end, the agent clears the answer buffer so late arrivals can't leak into the next roun

7. The agent prints structured feedback after each window.

   * The output sequence is the winner line, the scoreboard, and either the next question or the reset message.
   * `ScoreKeeper.render(top_n=5)` shows a compact list of leaders. When empty, it shows a placeholder.

> 📝 **Note:**
>
> * Only the first submission per `remote_addr` is counted within a window.
> * The 5 second window uses a monotonic clock to avoid wall-time jumps.
> * Late answers from previous questions are ignored because only the current question index is eligible when picking a winner.
> * Included sets: [qa_foundations.json](./qa_foundations.json) and [qa_sysdesign_gradual.json](./qa_sysdesign_gradual.json). Any file with the same structure works with `--qa`.

</details>

## SDK Features Used


| Feature                                                                              | Description                                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SummonerClient(name=...)`                                                           | Instantiates the client and sets up logging/context for the agent.                                                                                                                                                                           |
| `client.flow().activate()`                                                           | Enables the **flow engine** so route strings can drive which `@receive` handlers run.                                                                                                                                                        |
| `client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")` | Declares how arrows are parsed (e.g., `none --> started`); matches the route strings used in this agent.                                                                                                                                     |
| `client_flow.triggers()`                                                             | Loads trigger symbols (e.g., `ok`) from the `TRIGGERS` file. Used as `Move(Trigger.ok)` / `Stay(Trigger.ok)` to signal transitions or stick with the current state.                                                                                   |
| `@client.upload_states()`                                                            | Reports the current local **`phase`** (`"none"` or `"started"`) to the flow engine. This is what the engine matches against the **source** of each route.                                                                                    |
| `@client.download_states()`                                                          | Receives the **allowed next nodes** aggregated from handlers and folds them back into `phase` (e.g., set to `"started"` when `Node("started")` is present; otherwise `"none"`).                                                              |
| `@client.hook(Direction.RECEIVE)`                                                    | Validates/logs inbound payloads *once* before routing: prints text; **drops** messages starting with `Warning:`; forwards only well-shaped dicts (with `content`/`remote_addr`) to the receivers; raw strings are shown then filtered out.   |
| `@client.receive(route="none --> started")`                                          | **Start trigger.** Any valid inbound message moves the flow to `started`; `Q#0` will publish on the next send tick.                                                                                                                          |
| `@client.receive(route="started")`                                                   | Accepts answer labels during a round; enqueues `(addr, idx, pts, ts)` for the **current** question. If a label arrives before `Q#0` is published, it's ignored gracefully.                                                                   |
| `Move` / `Stay` (return type `Event`)                                                | Handlers return `Move(Trigger.ok)` to transition (`none → started`) and `Stay(Trigger.ok)` to remain in place while processing answers.                                                                                                      |
| `@client.send(route="")`                                                             | Publishes questions, opens a 5-second answer window starting at the first answer, applies tie-breaks (first per address → current-question answers → higher points → earlier time), updates the scoreboard, and resets the round after 2 Qs. |
| `client.loop.run_until_complete(setup())`                                            | Initializes shared structures on the client loop (e.g., `answer_buffer = asyncio.Queue()`, `variables_lock = asyncio.Lock()`). |
| `client.run(...)`                                                                    | Connects to the server and runs the event loop that drives both receive and send pipelines.                                                                                                                                                  |

> [!NOTE]
> Local helpers from [exam_utils.py](./exam_utils.py):
>
> * `Style` for optional ANSI output.
> * `Questions` for loading, rendering, and scoring.
> * `ScoreKeeper` for accumulation and rendering.
> * `Countdown` for a local grading timer.

> [!NOTE]  
> **About `Trigger`:** `client_flow.triggers()` builds a namespace from the plain-text file `TRIGGERS`. Each non-empty, non-comment line defines one symbol. For example, if the file contains `ok`, you can reference it as `Trigger.ok` in `Move(Trigger.ok)` or `Stay(Trigger.ok)`. To add more (e.g., `error`, `ignore`), place them on separate lines in the file and use them the same way in your handlers.


## How to Run

First start the Summoner server:

```bash
python server.py
```

> [!TIP]
> For cleaner terminal output and log files, you can pass
> `--config configs/server_config_nojsonlogs.json` to the server and clients.

Then run the exam agent by itself with one of the included question sets:

```bash
# Example: systems design set
python agents/agent_ExamAgent_1/agent.py \
  --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
```

> The agent will publish questions when any participant sends a message. Use a chat agent in another terminal to answer.

### CLI options

| Flag                 | Meaning                                          | Default                      |
| -------------------- | ------------------------------------------------ | ---------------------------- |
| `--qa PATH`          | Path to the questions JSON file                  | required                     |
| `--colors <0 OR 1>`  | ANSI color output for prompts, scoreboard, and status | `1`                      |
| `--countdown <0 OR 1>` | Show a five-second local countdown during grading windows | `1` |
| `--config PATH`      | Optional client config JSON for `SummonerClient` | `configs/client_config.json` |



## Simulation Scenarios

The included files [qa_foundations.json](./qa_foundations.json) and [qa_sysdesign_gradual.json](./qa_sysdesign_gradual.json) follow this format:

```json
[
  {
    "question": "Text of question...",
    "answers": {
      "A": { "val": "Choice text", "pts": 5 },
      "B": { "val": "Choice text", "pts": 3 },
      "C": { "val": "Choice text", "pts": 1 }
    }
  }
]
```

Labels are compared case-insensitively. Any compatible file in this format will work with `--qa`.

### Scenario 1 — one chat agent answering two questions

Uses `qa_sysdesign_gradual.json`. The agent is configured with `limit=2`. After two questions, the scoreboard resets. Only the first submission per address counts.

```bash
# terminal 1
python server.py

# terminal 2
python agents/agent_ChatAgent_3/agent.py

# terminal 3
python agents/agent_ExamAgent_1/agent.py --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
```

**Step 1 — send any message to start the round**

   * Chat agent sends a free-form message. This triggers the exam agent to publish `Q#0`.

   **Terminal 2 (ChatAgent_3)**

   ```
   python agents/agent_ChatAgent_3/agent.py
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:00:08.921 - ChatAgent_3 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [opened]> this is to sart (any message works)
   ```

   **Terminal 3 (ExamAgent_1) so far**

   ```
   python agents/agent_ExamAgent_1/agent.py --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:00:13.789 - ExamAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [Received] 127.0.0.1:63371 answered: this is to sart (any message works)
   ```

   * After the trigger, the chat agent receives `Q#0`.

   **Terminal 2 (ChatAgent_3) continues**

   ```
   [Received] Q#0: HTTP caching: which strategy best balances freshness and scalability for mostly-static assets with occasional updates?
   A) Versioned URLs (content hashes) with long max-age and immutable; re-deploy bumps the URL.
   B) Short max-age for everything to ensure quick refresh everywhere.
   C) Always use no-store so clients fetch every time.
   D) Disable caching globally and rely on CDN origin shielding only.
   (Answer with the label. 5s window after first answer.)
   ```

**Step 2 — answer `Q#0`**

   * Chat agent answers with a label (`A`, `B`, `C`, or `D`). The exam agent records it; the 5-second window begins on the **first** answer. The local countdown (if enabled) shows on the exam agent terminal and clears after the window ends.

   **Terminal 2 (ChatAgent_3)**

   ```
   [opened]> A
   ```

   **Terminal 3 (ExamAgent_1) so far**

   ```
   [Received] 127.0.0.1:63371 answered: A
   ```

   * After the 5-second window closes, the exam agent publishes the result, scoreboard, and `Q#1`. The chat agent receives them.

   **Terminal 2 (ChatAgent_3) receives result + next question**

   ```
   [Received] Winner: 127.0.0.1:63371 best answered Q#0, earning 5 points.

   Scoreboard:
   1. 127.0.0.1:63371 — 5 pts

   Q#1: Backoff strategy: which is generally the safest for client retries at scale?
   A) Linear backoff with a fixed small delay.
   B) No retries; let users manually retry.
   C) Immediate retry loop until success.
   D) Exponential backoff with jitter (full or decorrelated).
   (Answer with the label. 5s window after first answer.)
   ```

**Step 3 — answer `Q#1`**

   * Chat agent answers the second question. The exam agent records it; the 5-second window runs again.

   **Terminal 2 (ChatAgent_3)**

   ```
   [opened]> B
   ```

   **Terminal 3 (ExamAgent_1) so far**

   ```
   [Received] 127.0.0.1:63371 answered: B
   ```

   * After the window closes, the exam agent publishes the final result and resets the scoreboard because the round used `limit=2`.

   **Terminal 2 (ChatAgent_3) final output**

   ```
   [Received] Winner: 127.0.0.1:63371 best answered Q#1, earning 1 points.

   Scoreboard:
   1. 127.0.0.1:63371 — 6 pts

   Scoreboard reset — new round begins!

   [opened]> 
   ```

   **Terminal 3 (ExamAgent_1) final log for this run**

   ```
   [Received] 127.0.0.1:63371 answered: this is to sart (any message works)
   [Received] 127.0.0.1:63371 answered: A
   [Received] 127.0.0.1:63371 answered: B  
   ```

   > [!TIP]
   > The JSON file used here is `qa_sysdesign_gradual.json`. Any custom file with the documented format (list of questions, labeled answers with `val` and `pts`) will work with `--qa`.


### Scenario 2: two chat agents competing

Two chat agents race to answer. The exam agent enforces first-submission-per-address. The window begins on the first answer. Uses `qa_sysdesign_gradual.json`.

```bash
# terminal 1
python server.py

# terminal 2
python agents/agent_ChatAgent_3/agent.py

# terminal 3
python agents/agent_ChatAgent_3/agent.py

# terminal 4
python agents/agent_ExamAgent_1/agent.py --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
```

**Step 1: send any message to start the round**

   * Terminal 2 sends a free-form message. This triggers the exam agent to publish `Q#0`.

   **Terminal 2**

   ```
   python agents/agent_ChatAgent_3/agent.py
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:08:43.999 - ChatAgent_3 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [opened]> hey
   ```

   **Terminal 4 (ExamAgent_1) so far**

   ```
   python agents/agent_ExamAgent_1/agent.py --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:08:41.986 - ExamAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [Received] 127.0.0.1:63482 answered: hey
   ```

   * Both chat agents now receive `Q#0`.

   **Terminal 2 (receives `Q#0`)**

   ```
   [Received] Q#0: HTTP caching: which strategy best balances freshness and scalability for mostly-static assets with occasional updates?
   A) Versioned URLs (content hashes) with long max-age and immutable; re-deploy bumps the URL.
   B) Short max-age for everything to ensure quick refresh everywhere.
   C) Always use no-store so clients fetch every time.
   D) Disable caching globally and rely on CDN origin shielding only.
   (Answer with the label. 5s window after first answer.)
   ```

   **Terminal 3 (receives `Q#0`)**

   ```
   python agents/agent_ChatAgent_3/agent.py
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:08:43.107 - ChatAgent_3 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [Received] hey
   [Received] Q#0: HTTP caching: which strategy best balances freshness and scalability for mostly-static assets with occasional updates?
   A) Versioned URLs (content hashes) with long max-age and immutable; re-deploy bumps the URL.
   B) Short max-age for everything to ensure quick refresh everywhere.
   C) Always use no-store so clients fetch every time.
   D) Disable caching globally and rely on CDN origin shielding only.
   (Answer with the label. 5s window after first answer.)
   ```


**Step 2: answer `Q#0` (window starts on first answer)**

   * Terminal 2 answers with `A`.
   * Terminal 3 answers with `B`.
   * The 5-second window begins when the first answer arrives. If countdown is enabled, it appears on Terminal 4 and clears after the window ends.

   **Terminal 2**

   ```
   [opened]> A
   ```

   **Terminal 3**

   ```
   [opened]> B
   ```

   **Terminal 4 (ExamAgent_1) so far**

   ```
   [Received] 127.0.0.1:63482 answered: A
   [Received] 127.0.0.1:63480 answered: B
   ```

**Step 3: window closes for `Q#0`, result and scoreboard are published, then `Q#1` is sent**

   * The exam agent applies tie-break rules and declares the winner for `Q#0`, then sends `Q#1` to everyone.

   **Terminal 2 (receives result + `Q#1`)**

   ```
   [Received] Winner: 127.0.0.1:63482 best answered Q#0, earning 5 points.

   Scoreboard:
   1. 127.0.0.1:63482 — 5 pts

   Q#1: Backoff strategy: which is generally the safest for client retries at scale?
   A) Linear backoff with a fixed small delay.
   B) No retries; let users manually retry.
   C) Immediate retry loop until success.
   D) Exponential backoff with jitter (full or decorrelated).
   (Answer with the label. 5s window after first answer.)
   ```

   **Terminal 3 (receives result + `Q#1`)**

   ```
   [Received] Winner: 127.0.0.1:63482 best answered Q#0, earning 5 points.

   Scoreboard:
   1. 127.0.0.1:63482 — 5 pts

   Q#1: Backoff strategy: which is generally the safest for client retries at scale?
   A) Linear backoff with a fixed small delay.
   B) No retries; let users manually retry.
   C) Immediate retry loop until success.
   D) Exponential backoff with jitter (full or decorrelated).
   (Answer with the label. 5s window after first answer.)
   ```

**Step 4: answer `Q#1` (second window starts)**

   * Terminal 2 answers with `C`.
   * Terminal 3 answers with `B`.
   * The window again runs for 5 seconds from the first answer.

   **Terminal 2**

   ```
   [opened]> C
   ```

   **Terminal 3**

   ```
   [opened]> B
   ```

   **Terminal 4 (ExamAgent_1) so far**

   ```
   [Received] 127.0.0.1:63482 answered: C
   [Received] 127.0.0.1:63480 answered: B
   ```

**Step 5: window closes for `Q#1`, final result and round reset**

   * The exam agent publishes the result for `Q#1` and shows the cumulative scoreboard. Because `limit=2`, the scoreboard then resets and the round ends. Any new message will start a new round.

   **Terminal 2 (final for this round)**

   ```
   [Received] Winner: 127.0.0.1:63480 best answered Q#1, earning 1 points.

   Scoreboard:
   1. 127.0.0.1:63482 — 5 pts
   2. 127.0.0.1:63480 — 1 pts

   Scoreboard reset — new round begins!

   [opened]> 
   ```

   **Terminal 3 (final for this round)**

   ```
   [Received] Winner: 127.0.0.1:63480 best answered Q#1, earning 1 points.

   Scoreboard:
   1. 127.0.0.1:63482 — 5 pts
   2. 127.0.0.1:63480 — 1 pts

   Scoreboard reset — new round begins!

   [opened]> 
   ```

   **Terminal 4 (ExamAgent_1) final log for this run**

   ```
   python agents/agent_ExamAgent_1/agent.py --qa agents/agent_ExamAgent_1/qa_sysdesign_gradual.json
   [DEBUG] Loaded config from: configs/client_config.json
   2025-08-22 13:08:41.986 - ExamAgent_1 - INFO - Connected to server @(host=127.0.0.1, port=8888)
   [Received] 127.0.0.1:63482 answered: hey
   [Received] 127.0.0.1:63482 answered: A
   [Received] 127.0.0.1:63480 answered: B
   [Received] 127.0.0.1:63482 answered: C
   [Received] 127.0.0.1:63480 answered: B
   ```

   > [!NOTE]
   >
   > * Only the first submission per `remote_addr` counts within each window.
   > * The countdown appears on the exam agent terminal if `--countdown 1` and clears after each window.
   > * The JSON file used here is `qa_sysdesign_gradual.json`. Any custom file with the documented format works with `--qa`.

