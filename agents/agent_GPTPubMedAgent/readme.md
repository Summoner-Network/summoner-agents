# `GPTPubMedAgent`

A guarded GPT powered agent that decides whether to call the **PubMed / NCBI E-utilities API** and, when appropriate, returns a structured response describing one or several scientific articles. It composes a prompt from a **personality** and a **format directive**, then uses the GPT output as parameters for an async `pubmed_handle_request` helper. If no PubMed call is needed, it returns a small diagnostic payload instead.

It demonstrates how to:

* subclass `SummonerClient`,
* use receive/send hooks with a buffer,
* integrate cost/token guardrails (see [`safeguards.py`](./safeguards.py)),
* load prompts from [`gpt_config.json`](./gpt_config.json),
* use GPT to decide whether to call an external API and with which ranking (`latest` vs `most relevant`),
* call the **PubMed E-utilities** via `aiohttp` (`esearch.fcgi` + `efetch.fcgi`) and return normalized article summaries.

The agent also uses an identity tag from [`id.json`](./id.json) and is designed to interoperate with agents that send structured content (e.g., [`InputAgent`](../agent_InputAgent/)).

> [!NOTE]
> The overall structure is inspired by [`EchoAgent_2`](../agent_EchoAgent_2/) and built from its GPT-based adaptation [`GPTRespondAgent`](../agent_GPTRespondAgent/) by changing its prompt and adding an API call layer to the PubMed E-utilities to fulfill biomedical literature lookup requests.

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
> **Optional `NCBI_API_KEY` for PubMed.**
> PubMed works without an API key, but with lower rate limits. To increase limits, you can obtain an NCBI API key:
>
> 1. Follow the instructions here: [https://support.nlm.nih.gov/kbArticle/?pn=KA-05317](https://support.nlm.nih.gov/kbArticle/?pn=KA-05317).
> 2. Log into your [NCBI account](https://www.ncbi.nlm.nih.gov/account/).
> 3. Click your username/email (top right) → **Account Settings**.
> 4. Scroll to the bottom and generate an **API key**.
> 5. Store it in your `.env` as:
>
>    ```env
>    NCBI_API_KEY=your_ncbi_api_key_here
>    ```
>
> Ensure your `.env` is in `.gitignore`. When `NCBI_API_KEY` is not set, the agent still works, but within unauthenticated rate limits.

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. On startup, the `setup` coroutine initializes an `asyncio.Queue` named `message_buffer`.

2. `MyAgent`, a subclass of `SummonerClient`, loads:

   * OpenAI API key from environment (via `dotenv` if present),
   * optionally an NCBI key from `NCBI_API_KEY` (for higher PubMed rate limits),
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

   * an **empty object** `{}` meaning *do not call PubMed*, or
   * a dict describing a PubMed operation, for example:

     ```json
     {
       "action": "search_fetch",
       "term": "combinatorial genome-wide association study",
       "retmax": 1,
       "sort": "pub_date"
     }
     ```

     or

     ```json
     {
       "action": "search_fetch",
       "term": "Alzheimer's disease synaptic loss",
       "retmax": 3,
       "sort": "relevance"
     }
     ```

   The send handler then:

   * checks if `tool_args` is non-empty and contains a non-empty `"action"` string,
   * if yes, calls:

     ```python
     api_result = await pubmed_handle_request(tool_args)
     performed_call = True
     ```
   * if no, it sets:

     ```python
     api_result = {
         "error": "no_pubmed_call_requested_or_missing_action",
         "tool_args": tool_args,
     }
     performed_call = False
     ```

8. The `pubmed_handle_request` helper internally:

   * normalizes `retmax` and `sort`:

     * `sort="pub_date"` → **latest** by publication date,
     * `sort="relevance"` → **most relevant** (best match),
   * if explicit PMIDs are provided (`pmid` or `pmids`), it calls **EFetch** directly,
   * otherwise, it calls **ESearch** with `term`, then **EFetch** on returned PMIDs,
   * normalizes each article to:

     ```json
     {
       "pmid": "12345678",
       "title": "Article title...",
       "journal": "Journal name",
       "date": "2024",
       "authors": ["First Author", "Second Author"],
       "abstract": "Abstract text...",
       "link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
     }
     ```

9. The agent sends back a normalized response of the form:

   ```json
   {
     "tool": "pubmed",
     "performed_call": true,
     "result": {
       "action": "search_fetch",
       "term": "combinatorial genome-wide association study",
       "pmids": ["12345678"],
       "retmax": 1,
       "sort": "pub_date",
       "count": 1,
       "articles": [
         {
           "pmid": "12345678",
           "title": "Example combinatorial GWAS paper title",
           "journal": "Example Journal",
           "date": "2023",
           "authors": ["First Author", "Second Author"],
           "abstract": "Short abstract text...",
           "link": "https://pubmed.ncbi.nlm.nih.gov/12345678/"
         }
       ],
       "timestamp_utc": "2025-12-04T12:00:00Z"
     },
     "tool_args": {
       "action": "search_fetch",
       "term": "combinatorial genome-wide association study",
       "retmax": 1,
       "sort": "pub_date"
     },
     "to": "<uuid of sender>"
   }
   ```

   If no lookup is performed or if inputs are incomplete, `result` contains an error payload such as:

   ```json
   {
     "error": "missing_term_and_pmids",
     "tool_args": { ... }
   }
   ```

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
| `@agent.send(route=...)`             | Builds the GPT prompt, interprets output as tool args, conditionally calls PubMed, returns result |
| `agent.logger`                       | Logs hook activity, buffering, PubMed calls, and send lifecycle events                            |
| `agent.loop.run_until_complete(...)` | Runs the `setup` coroutine to initialize the message queue                                        |
| `agent.run(...)`                     | Connects to the server and starts the asyncio event loop                                          |


## How to Run

First, start the Summoner server:

```bash
python server.py
```

> [!TIP]
> You can use the option `--config configs/server_config_nojsonlogs.json` for cleaner terminal output and log files.

Prepare `gpt_config.json` and `id.json` in `agents/agent_GPTPubMedAgent/`.

A **typical `gpt_config.json` for GPTPubMedAgent** (only the important parts):

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

  "format_prompt": "You will receive ONE JSON object under the label \"Content:\". This object may include fields such as \"question\", \"instruction\", \"keywords\", \"pmid\", \"pmids\", \"mode\" (e.g. \"latest\" or \"most relevant\"), or other context describing what the user wants.\n\nYour task:\n1) Decide whether the user is asking to look up scientific articles in PubMed or would clearly benefit from such a lookup. Cues include disease/biology topics plus terms like \"PubMed\", \"recent paper\", \"latest trial\", \"most relevant article\", or explicit PMIDs.\n2) If a PubMed lookup IS appropriate and you can identify the necessary parameters, construct arguments for the PubMed helper and OUTPUT a JSON object with the following keys:\n   - \"action\": always the string \"search_fetch\".\n   - One of:\n       * \"term\": a STRING keyword query suitable for PubMed ESearch (you may include PubMed field tags like [tiab], [dp], etc. if the request implies them), OR\n       * \"pmid\": a single PMID as a string, OR\n       * \"pmids\": a LIST of PMIDs as strings.\n   - \"retmax\": an INTEGER between 1 and 20 (optional; default is 5). Use this when the user asks for the \"top 1\", \"top 3\", \"a few papers\", etc.\n   - \"sort\": a STRING describing the ranking, with allowed values:\n       * \"pub_date\" for latest/newest by publication date,\n       * \"relevance\" for most relevant / best match.\n     Map user language like \"latest\", \"most recent\", \"newest\" to \"pub_date\" and \"most relevant\", \"best match\" to \"relevance\".\n3) If a PubMed lookup is NOT appropriate, or if you cannot reliably infer either a search term or any PMIDs, OUTPUT an EMPTY JSON object: {}.\n\nRules:\n- Output MUST be a single JSON object.\n- If you decide to call PubMed, you MUST include \"action\": \"search_fetch\" and at least one of \"term\", \"pmid\", or \"pmids\".\n- You MAY include \"retmax\" and \"sort\" when the user implies a number of results or a preference for latest vs most relevant; otherwise you may omit them.\n- Do NOT include any keys other than: \"action\", \"term\", \"pmid\", \"pmids\", \"retmax\", \"sort\".\n- Do NOT add explanations, comments, or natural-language text outside the JSON. The entire response must be valid JSON.\n- Use only the information present in Content and general reasoning. You do not call the API; you only prepare the parameters.\n\nExamples:\n- User asks: \"Give me the latest paper on CRISPR therapy for sickle cell disease\" → {\"action\": \"search_fetch\", \"term\": \"CRISPR therapy sickle cell disease\", \"retmax\": 1, \"sort\": \"pub_date\"}\n- User asks: \"Find the most relevant recent papers on machine learning for cancer prognosis, maybe 3 articles\" → {\"action\": \"search_fetch\", \"term\": \"machine learning cancer prognosis\", \"retmax\": 3, \"sort\": \"relevance\"}\n- User asks: \"Look up PMID 34567890 on PubMed\" → {\"action\": \"search_fetch\", \"pmid\": \"34567890\", \"retmax\": 1}\n- User asks: \"Explain what PubMed is\" (no lookup requested) → {}"
}
```

The agent identity is defined in `id.json` and only requires a `"uuid"` key:

```json
// agents/agent_GPTPubMedAgent/id.json
{"uuid": "6fb3fedd-ebca-43b8-b915-fd25a6ecf78a"}
```

Start the agent:

```bash
python agents/agent_GPTPubMedAgent/agent.py
```

Optional CLI flags:

* `--gpt <path>`: Use a custom `gpt_config.json` path.
* `--id <path>`: Use a custom `id.json` path.
* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).


## Simulation Scenarios

These scenarios show how `GPTPubMedAgent` consumes input from `InputAgent` and either:

* calls PubMed when a literature lookup is appropriate, or
* returns a small diagnostic payload when it is not.

All scenarios use `InputAgent` so you can type requests interactively and inspect the resulting payloads.

```bash
# Terminal 1: server
python server.py

# Terminal 2: InputAgent (multi-line input)
python agents/agent_InputAgent/agent.py --multiline 1

# Terminal 3: GPTPubMedAgent
python agents/agent_GPTPubMedAgent/agent.py
```

### Scenario A — InputAgent, latest paper on combinatorial GWAS

In Terminal 2 (`InputAgent`), type:

```text
> Give me the latest PubMed paper on combinatorial GWAS in complex disease.
```

`GPTPubMedAgent` should:

1. Receive this as `content` (a string).

2. Have GPT map this to something like:

   ```json
   {
     "action": "search_fetch",
     "term": "combinatorial GWAS complex disease",
     "retmax": 1,
     "sort": "pub_date"
   }
   ```

3. Call PubMed ESearch (with `sort=pub_date`, `retmax=1`), then EFetch the returned PMID.

4. Return a payload similar to:

```log
[Received] {
  'tool': 'pubmed',
  'performed_call': True,
  'result': {
    'action': 'search_fetch',
    'term': 'combinatorial genome-wide association study complex disease',
    'pmids': ['12345678'],
    'retmax': 1,
    'sort': 'pub_date',
    'count': 1,
    'articles': [
      {
        'pmid': '12345678',
        'title': 'Combinatorial genome-wide association approaches for complex trait mapping',
        'journal': 'Example Journal of Genetics',
        'date': '2023',
        'authors': ['First Author', 'Second Author'],
        'abstract': 'Short abstract text summarizing the combinatorial GWAS approach...',
        'link': 'https://pubmed.ncbi.nlm.nih.gov/12345678/'
      }
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'search_fetch',
    'term': 'combinatorial genome-wide association study complex disease',
    'retmax': 1,
    'sort': 'pub_date'
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

This scenario checks that **"latest"** is correctly mapped to `sort="pub_date"` and `retmax=1`.


### Scenario B — InputAgent, most relevant papers on Alzheimer's disease

In Terminal 2, type:

```text
> Find the most relevant PubMed papers on Alzheimer's disease focusing on synaptic loss. Show me about 3 articles.
```

`GPTPubMedAgent` should interpret this as a PubMed search with a relevance ranking and GPT should output tool args similar to:

```json
{
  "action": "search_fetch",
  "term": "Alzheimer's disease synaptic loss",
  "retmax": 3,
  "sort": "relevance"
}
```

The agent calls ESearch with `sort=relevance`, then EFetches up to 3 PMIDs. You should see a response like:

```log
[Received] {
  'tool': 'pubmed',
  'performed_call': True,
  'result': {
    'action': 'search_fetch',
    'term': "Alzheimer's disease synaptic loss",
    'pmids': ['34567890', '34567901', '34567912'],
    'retmax': 3,
    'sort': 'relevance',
    'count': 3,
    'articles': [
      {
        'pmid': '34567890',
        'title': 'Synaptic degeneration in Alzheimer\'s disease: mechanisms and therapeutic targets',
        'journal': 'Example Neuroscience Journal',
        'date': '2022',
        'authors': ['First Author', 'Second Author'],
        'abstract': 'Short abstract focusing on synaptic loss mechanisms...',
        'link': 'https://pubmed.ncbi.nlm.nih.gov/34567890/'
      },
      {
        'pmid': '34567901',
        'title': 'Early synaptic changes in Alzheimer\'s disease models',
        'journal': 'Brain Research',
        'date': '2021',
        'authors': ['Third Author'],
        'abstract': 'Abstract text...',
        'link': 'https://pubmed.ncbi.nlm.nih.gov/34567901/'
      },
      {
        'pmid': '34567912',
        'title': 'Linking amyloid and synaptic dysfunction in Alzheimer\'s disease',
        'journal': 'Neurobiology of Disease',
        'date': '2020',
        'authors': ['Fourth Author', 'Fifth Author'],
        'abstract': 'Abstract text...',
        'link': 'https://pubmed.ncbi.nlm.nih.gov/34567912/'
      }
    ],
    'timestamp_utc': '2025-12-04T12:00:00Z'
  },
  'tool_args': {
    'action': 'search_fetch',
    'term': "Alzheimer's disease synaptic loss",
    'retmax': 3,
    'sort': 'relevance'
  },
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

This scenario checks that the agent can:

* recognize "most relevant" → `sort="relevance"`,
* map "about 3 articles" → `retmax=3`.

---

### Scenario C — InputAgent, no PubMed call requested

In Terminal 2 (`InputAgent`), type:

```text
> {"instruction":"Explain in simple terms what a genome-wide association study (GWAS) is."}
```

Here the user is asking for a general explanation, not a specific PubMed lookup. The `format_prompt` tells GPT to only request a PubMed call when an actual literature search clearly makes sense.

In this case, GPT should output `{}` as tool args, the agent will not call PubMed, and the response will look like:

```log
[Received] {
  'tool': 'pubmed',
  'performed_call': False,
  'result': {
    'error': 'no_pubmed_call_requested_or_missing_action',
    'tool_args': {}
  },
  'tool_args': {},
  'from': '6fb3fedd-ebca-43b8-b915-fd25a6ecf78a'
}
>
```

---

You can use these three scenarios to verify:

* that PubMed is called when the intent is clearly "look up biomedical papers" (with appropriate **latest** vs **most relevant** behavior), and
* that no call is made when the request is purely explanatory and does not require a PubMed lookup.
