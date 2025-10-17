# `GPTClusterAgent`

A guarded **embedding-and-clustering** agent that takes an iterable of texts, computes embeddings, and clusters them with a configurable algorithm. It demonstrates how to subclass `SummonerClient`, use receive/send hooks with a buffer, integrate token/cost guardrails for embeddings (see [`safeguards.py`](./safeguards.py)), and load settings from [`gpt_config.json`](./gpt_config.json). The agent also uses an identity tag from [`id.json`](./id.json). This agent is designed to interoperate (and works best) with agents that can send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by and built from [`EchoAgent_2`](../agent_EchoAgent_2/), adapted for embedding and clustering.

> [!IMPORTANT]
> **OpenAI credentials required.** Both agents call `load_dotenv()` and expect an environment variable named `OPENAI_API_KEY`. Put a `.env` file at the **project root** (or set the variable in your shell/CI) so it’s available at runtime:
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
   * **config** from `gpt_config.json` (or `--gpt <path>`), including:

     * `embedding_model`, `max_embedding_input_tokens`, `embed_cost_limit_usd`, `debug`, `sleep_seconds`,
     * default `clustering` parameters (e.g., `algo`, `k`, `max_iter`, `seed`),
   * An identity UUID (`my_id`) from `id.json` (or `--id <path>`).

3. Incoming messages invoke the receive-hook (`@client.hook(Direction.RECEIVE)`):

   * If it’s a string starting with `"Warning:"`, logs a warning and drops it.
   * If it’s not a dict with `"remote_addr"` and `"content"`, logs:

     ```
     [hook:recv] missing address/content
     ```

     and drops it.
   * Otherwise, logs:

     ```
     [hook:recv] <addr> passed validation
     ```

     and forwards the message to the receive handler.

4. The receive handler (`@client.receive(route="")`) serializes `content`, enqueues it into `message_buffer`, and logs:

   ```
   Buffered message from:(SocketAddress=<addr>).
   ```

5. Before sending, the send-hook (`@client.hook(Direction.SEND)`) logs:

   ```
   [hook:send] sign <uuid>
   ```

   It wraps raw strings into `{"message": ...}`, adds `{"from": my_id}`, and forwards the message to the send handler.

6. The send handler (`@client.send(route="")`) dequeues the payload, expecting content of the form:

   ```json
   {"texts": [...], "clustering": { /* optional overrides */ }}
   ```

   Then it:

   * **Embeds** the texts with token/cost guardrails using the configured `embedding_model`.

     * Prints diagnostics when `debug` is true:

       ```
       Embedding tokens: <n> > <max_embedding_input_tokens> ? <True|False>
       [embed] Estimated cost: $<...>
       [embed] Actual cost: $<...>   (when available)
       ```
     * Aborts the call if tokens exceed `max_embedding_input_tokens` or the estimated cost exceeds `embed_cost_limit_usd`.
   * **Clusters** the embeddings using the requested algorithm (message-level override via `content.clustering` or defaults from config):

     * `"kmeans"` (default): `{"k": int, "max_iter": int, "seed": int}`
     * `"agglomerative"`: `{"k": int, "linkage": "ward|complete|average|single", "metric": "euclidean|..."}`
     * `"dbscan"`: `{"eps": float, "min_samples": int, "metric": "euclidean|..."}`
     * If `scikit-learn` is unavailable, it falls back to a single cluster with a note.
   * Returns a JSON payload:

     ```json
     {
       "embeddings_cost": <float_or_null>,
       "embedding_model": "<model_name>",
       "num_texts": <int>,
       "result": {
         "algo": "<algo>",
         "assignments": [<int>, ...],
         "clusters": {"0": [<idx>, ...], "1": [<idx>, ...]},
         ...
       }
     }
     ```

   Logs a summary:

   ```
   [cluster] model=<embedding_model> id=<uuid> texts=<count>
   ```

7. Sleeps for `sleep_seconds` and repeats until stopped (Ctrl+C).

</details>

## SDK Features Used

| Feature                               | Description                                                             |
| ------------------------------------- | ----------------------------------------------------------------------- |
| `class MyAgent(SummonerClient)`       | Subclasses `SummonerClient` to load configs, identity, and manage state |
| `@client.hook(Direction.RECEIVE)`     | Validates or drops incoming messages before main handling               |
| `@client.hook(Direction.SEND)`        | Signs outgoing messages by adding a `from` field with UUID              |
| `@client.receive(route=...)`          | Buffers validated messages into the queue                               |
| `@client.send(route=...)`             | Embeds texts, applies guardrails, clusters, and returns results         |
| `client.logger`                       | Logs hook activity, buffering, and send lifecycle events                |
| `client.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue              |
| `client.run(...)`                     | Connects to the server and starts the asyncio event loop                |

## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json`. A typical `gpt_config.json` looks like:

```json
{
  "embedding_model": "text-embedding-3-small",
  "max_embedding_input_tokens": 500,
  "embed_cost_limit_usd": 0.0008,
  "debug": true,
  "sleep_seconds": 0.1,
  "clustering": {
    "algo": "kmeans",
    "k": 3,
    "max_iter": 20,
    "seed": 0
  }
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTClusterAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTClusterAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).

## Simulation Scenarios

This scenario shows how `GPTClusterAgent` consumes structured input from `InputAgent` and returns a clustering result.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTClusterAgent
python agents/agent_GPTClusterAgent/agent.py
```

**Scenario A — Default k-means with k=3:**
In Terminal 2 (InputAgent), type:

```
> {"texts":["red apple","green apple","ripe banana","sports car","family sedan","pickup truck"]}
```

Terminal 3 (GPTClusterAgent) logs (when `debug: true`) and returns something like:

```
[cluster] model=text-embedding-3-small id=6fb3fedd-ebca-43b8-b915-fd25a6ecf78a texts=6
```

Terminal 2 receives a dict with assignments and clusters (example structure shown):

```
[Received] {'embeddings_cost': 2.4000000000000003e-07, 'embedding_model': 'text-embedding-3-small', 'num_texts': 6, 'result': {'algo': 'kmeans', 'k': 3, 'assignments': [2, 2, 1, 0, 0, 0], 'clusters': {'2': [0, 1], '1': [2], '0': [3, 4, 5]}}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
> 
```

**Scenario B — Override to k=2 for broader groups:**
In Terminal 2, type:

```
> {"texts":["red apple","green apple","ripe banana","sports car","family sedan","pickup truck"],"clustering":{"algo":"kmeans","k":2}}
```

Terminal 2 receives:

```
[Received] {'embeddings_cost': 2.4000000000000003e-07, 'embedding_model': 'text-embedding-3-small', 'num_texts': 6, 'result': {'algo': 'kmeans', 'k': 2, 'assignments': [1, 1, 1, 0, 0, 0], 'clusters': {'1': [0, 1, 2], '0': [3, 4, 5]}}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
> 
```

**Scenario C — Agglomerative example:**
In Terminal 2, type:

```
> {"texts":["strawberry jam","blueberry jam","raspberry jam","tennis match","soccer game","basketball playoffs"],"clustering":{"algo":"agglomerative","k":2,"linkage":"ward","metric":"euclidean"}}
```

Terminal 2 receives:

```
[Received] {'embeddings_cost': 4.0000000000000003e-07, 'embedding_model': 'text-embedding-3-small', 'num_texts': 6, 'result': {'algo': 'agglomerative', 'k': 2, 'linkage': 'ward', 'metric': 'euclidean', 'assignments': [1, 1, 1, 0, 0, 0], 'clusters': {'1': [0, 1, 2], '0': [3, 4, 5]}}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
```

**Scenario D — DBSCAN with noise handling:**
In Terminal 2, type:

```
> {"texts":["misc topic A","misc topic B","tight group X1","tight group X2","tight group X3"],"clustering":{"algo":"dbscan","eps":0.7,"min_samples":2}}
```

Terminal 2 receives:

```
[Received] {'embeddings_cost': 3.6e-07, 'embedding_model': 'text-embedding-3-small', 'num_texts': 5, 'result': {'algo': 'dbscan', 'eps': 0.7, 'min_samples': 2, 'metric': 'euclidean', 'assignments': [0, 0, 1, 1, 1], 'clusters': {'0': [0, 1], '1': [2, 3, 4]}}, 'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'}
```

> [!NOTE]
> DBSCAN may label some items as `-1` for noise; these will appear under the key `"-1"` in `clusters`.

**Scenario E — Guardrails (illustrative):**
If the combined input exceeds `max_embedding_input_tokens` (see [`gpt_config.json`](./gpt_config.json)), the agent prints:

```
Embedding tokens: 1200 > 500 ? True
[embed] Estimated cost: $0.0000xxxx
[embed] Tokens exceeded — unable to send the request.
```

and returns a payload with `result` set to a single cluster (or `None` embeddings if the request was skipped), depending on configuration.
