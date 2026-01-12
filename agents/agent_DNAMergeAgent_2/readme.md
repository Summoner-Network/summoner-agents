# `DNAMergeAgent_2`

A minimal Summoner client that **merges two partial agents from DNA JSON files**, without importing the original Python agent modules. The merged agent exposes multiple object/cell/arrow routes (two 4-cycles that share nodes) and renders tape occupancy in a browser window where **objects are nodes**, **arrows are directed edges**, and **arrow labels are circled bubbles attached to edges** (see [`summoner_web_viz.py`](./summoner_web_viz.py)). Occupied tokens in tape `states` are colored **green**; non-occupied tokens are **gray**.

> [!NOTE]
> This example is the "DNA-first" version of the merge demos:
>
> * `DNAMergeAgent_0` / `DNAMergeAgent_1` merge **imported live clients**.
> * `DNAMergeAgent_2` merges **pre-recorded DNA** (`agent_p1_dna.json`, `agent_p2_dna.json`).

The main components of this example are:

* **DNA JSON is the source of truth**: handler bodies, routes, trigger/action names, and an optional execution context header.
* **Merge from DNA** (`ClientMerger([{"dna_path": ...}, ...])`) compiles handler source into sandbox modules and registers them onto the merged client.
* **Context imports can be replayed** (`allow_context_imports=True`), so DNA can carry lightweight `from ... import ...` statements.
* **Missing runtime objects must be injected** (`rebind_globals={...}`), because the DNA context cannot serialize Python objects like `viz`, `Trigger`, or helper functions.
* **One-shot wiring** (`client.initiate_all()`) replays upload/download/receive/send/hooks into the merged client.

Relevant files:

* [`agent.py`](./agent.py) (merged client created from DNA JSON)
* [`agent_p1_dna.json`](./agent_p1_dna.json) (DNA for cycle `A → B → C → D → A`)
* [`agent_p2_dna.json`](./agent_p2_dna.json) (DNA for cycle `A → E → C → F → A`)
* [`dna.json`](./dna.json) (DNA dump produced by `client.dna(include_context=True)` after merge)
* [`summoner_web_viz.py`](./summoner_web_viz.py) (graph reconstruction + browser UI)

## Behavior

<details>
<summary><b>(Click to expand)</b> The agent goes through these steps:</summary>
<br>

1. The agent creates a visualizer instance:

   ```python
   viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)
   ```

2. It constructs runtime objects that the DNA will reference but cannot embed as JSON:

   ```python
   Trigger = load_triggers()
   OBJECTS = {Node(x) for x in ["A","B","C","D","E","F"]}

   def _content(msg):
       return msg.get("content") if isinstance(msg, dict) else msg
   ```

3. It builds the merged client directly from DNA JSON files:

   ```python
   client = ClientMerger(
       [
           {"dna_path": Path(__file__).resolve().parent / "agent_p1_dna.json"},
           {"dna_path": Path(__file__).resolve().parent / "agent_p2_dna.json"},
       ],
       name=AGENT_ID,
       allow_context_imports=True,
       verbose_context_imports=False,
       rebind_globals={
           "viz": viz,
           "_content": _content,
           "Event": Event,
           "Trigger": Trigger,
           "OBJECTS": OBJECTS,
       },
   )
   ```

   What is special here (vs imported-client merge):

   * Each DNA file starts with a `__context__` entry. In your files, that context lists imports and declares:

     * `var_name: "client"` (the name used in decorator lines like `@client.receive(...)`)
     * JSON-friendly globals like `state` and `AGENT_ID`
     * recipes like `OBJECTS = set(Node(...))`
     * a `missing` list that explicitly tells you what cannot be reconstructed from JSON (`Trigger`, `_content`, `viz`)
   * Because of that `missing` list, `rebind_globals` is not optional here. It is the mechanism that supplies those runtime values into the sandbox that compiles the handler functions.

4. The merged agent configures flow parsing and arrow syntax:

   ```python
   client_flow = client.flow().activate()
   client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
   ```

5. The merged agent replays everything from both DNA sources into a single client instance:

   ```python
   client.initiate_all()
   ```

6. At startup, the visualizer graph is built from merged DNA:

   ```python
   viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
   ```

   And the full merged DNA (including merged context) is exported to [`dna.json`](./dna.json):

   ```python
   json.dump(json.loads(client.dna(include_context=True)), open("dna.json","w"), indent=2)
   ```

7. Runtime command behavior is the same as the other merge demos:

   * `"ab"` triggers `A --[ab]--> B`
   * `"ae"` triggers `A --[ae]--> E`
   * etc.

   Each arrow receive returns either:

   * `Move(Trigger.ok)` if the command matches the arrow label
   * `Stay(Trigger.ok)` otherwise

</details>

## SDK Features Used

| Feature                             | Description                                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------------- |
| `ClientMerger([{"dna_path": ...}])` | Merges multiple DNA sources by compiling handler source in sandbox modules                  |
| `__context__` (DNA header)          | Optional DNA entry containing `imports`, JSON `globals`, and `recipes`                      |
| `allow_context_imports=True`        | Executes import lines embedded in DNA context (trusted DNA only)                            |
| `rebind_globals={...}`              | Injects runtime globals required by compiled DNA handlers (`viz`, `Trigger`, `_content`, …) |
| `client.flow().activate()`          | Enables flow parsing/dispatch                                                               |
| `flow.add_arrow_style(...)`         | Defines route syntax like `A --[ ab ]--> B`                                                 |
| `client.initiate_all()`             | Replays everything (upload/download/hooks/receivers/senders) into the merged client         |
| `client.dna(include_context=True)`  | Exports merged routes + execution context into a portable JSON form                         |

Here’s a **drop-in replacement** for the DNAMergeAgent_2 “How to Run” section that matches the **two-part structure** used in `DNACloneAgent_0`, while keeping your “DNA producer / DNA consumer” framing.


## How to Run

### 1) Produce the DNA files (one-time setup)

`DNAMergeAgent_2` does **not** import `agent_p1.py` / `agent_p2.py` as Python modules. Instead, it consumes two pre-exported DNA files:

* [`agent_p1_dna.json`](./agent_p1_dna.json)
* [`agent_p2_dna.json`](./agent_p2_dna.json)

You can generate these by running the original agents once, letting them export `client.dna(include_context=True)`, then shutting them down.

In other words:

* `agent_p1.py` and `agent_p2.py` are **DNA producers**
* `DNAMergeAgent_2` is a **DNA consumer**

Example (run each once):

```bash
python agents/agent_DNAMergeAgent_p1/agent_p1.py
python agents/agent_DNAMergeAgent_p2/agent_p2.py
```

After those two runs, you should have:

* `agents/agent_DNAMergeAgent_2/agent_p1_dna.json`
* `agents/agent_DNAMergeAgent_2/agent_p2_dna.json`

> [!NOTE]
> The exact filenames/paths depend on where your producer scripts write the JSON.
> The important part is that `DNAMergeAgent_2/agent.py` points to them via:
>
> ```python
> {"dna_path": Path(__file__).resolve().parent / "agent_p1_dna.json"}
> {"dna_path": Path(__file__).resolve().parent / "agent_p2_dna.json"}
> ```

### 2) Run the merged DNA consumer agent

First, start the Summoner server:

```bash
python server.py
```

Then run the DNA-based merged agent:

```bash
python agents/agent_DNAMergeAgent_2/agent.py
```

A browser window should open automatically at:

```
http://127.0.0.1:8765/
```

Optional CLI flag:

* `--config <path>`: Summoner **client** config path (defaults to `configs/client_config.json`).

Example:

```bash
python agents/agent_DNAMergeAgent_2/agent.py --config configs/client_config.json
```

## Simulation Scenarios

These scenarios run a minimal loop with a server, this merged agent, and an input-presenting agent.

```bash
# Terminal 1
python server.py

# Terminal 2
python agents/agent_DNAMergeAgent_2/agent.py

# Terminal 3
python agents/agent_InputAgent/agent.py
```

> [!NOTE]
> The visualizer builds the graph from **DNA** at startup:
>
> ```python
> viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
> ```
>
> The picture in the browser is derived from the same structure you can export to [`dna.json`](./dna.json), and that DNA is itself the merged union of the two partial agents ([`agent_p1.py`](./agent_p1.py), [`agent_p2.py`](./agent_p2.py)).

<p align="center">
  <img width="225" src="../../assets/img/dna_merge_p1.png" alt="..." style="vertical-align: middle;" />
  <span style="vertical-align: middle;">+</span>
  <img width="243" src="../../assets/img/dna_merge_p2.png" alt="..." style="vertical-align: middle;" />
  <span style="vertical-align: middle;">&rarr;</span>
  <img width="231" src="../../assets/img/dna_merge_whole.png" alt="..." style="vertical-align: middle;" />
</p>

### Scenario A — Merged traversal, showing "both parts react" on the same input

The point of this scenario is not "one clean cycle." The point is that a **single input** can trigger **multiple handlers** coming from the two original agents, because the merged client contains the union of their routes.

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_0.png"/>
</p>

#### Step 1: `ab`

In Terminal 3:

```
> ab
[Received] {'from': 'A', 'to': 'B', 'via': 'ab', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p1'}
[Received] {'node': 'A', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'A', 'to': 'E', 'via': 'ae', 'action': 'STAY', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* The merged agent is effectively running two sub-machines:

  * `agent_p1`: cycle `A → B → C → D → A`
  * `agent_p2`: cycle `A → E → C → F → A`
* Your single input `ab` is broadcast into the merged dispatch. In this run:

  * `agent_p1`’s arrow handler for `A --[ab]--> B` matched and emitted a **MOVE** trace (`A → B via ab`).
  * `agent_p1`’s object handler for `"A"` emitted a **TEST** trace (`node A`).
  * You also see `agent_p2` emit `A → E via ae` as **STAY**. This is the merged behavior: the second subgraph is present and can react in the same round. In terms of "links to the original parts," this third line is a direct fingerprint of code that lives in [`agent_p2.py`](./agent_p2.py).

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_1.png"/>
</p>

#### Step 2: `bc`

```
> bc
[Received] {'node': 'B', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'B', 'to': 'C', 'via': 'bc', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p1'}
```

What this means:

* These are purely `agent_p1` fingerprints.
* `agent_p1` recognizes `bc` as the arrow label for `B --[bc]--> C`, so it moves to **C**, and also tests object **B**.

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_2.png"/>
</p>

#### Step 3: `cd`

```
> cd
[Received] {'from': 'C', 'to': 'D', 'via': 'cd', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p1'}
[Received] {'node': 'C', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'C', 'to': 'F', 'via': 'cf', 'action': 'STAY', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* This step shows the "shared object" effect: **C is shared by both subgraphs**.
* You type `cd` (a cycle-1 command), and you see:

  * `agent_p1` moves `C → D via cd` and tests `C`.
  * `agent_p2` also emits `C → F via cf` as a stay trace (a direct fingerprint of code from [`agent_p2.py`](./agent_p2.py)).

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_3.png"/>
</p>

#### Step 4: `da`

```
> da
[Received] {'node': 'D', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'D', 'to': 'A', 'via': 'da', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p1'}
```

What this means:

* Back to pure `agent_p1` fingerprints.
* You complete `D → A` via `da`, and also test object `D`.

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_4.png"/>
</p>

### Scenario B — Cycle 2 traversal with "cycle 1 stays" visible

This scenario emphasizes a different kind of linkage: when you run a command that belongs to one subgraph, the other subgraph can still "react" by producing **STAY** traces on its own outgoing arrows.

#### Step 5: `ae`

```
> ae
[Received] {'from': 'A', 'to': 'B', 'via': 'ab', 'action': 'STAY', 'agent': 'DNAMergeAgent_p1'}
[Received] {'node': 'A', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'A', 'to': 'E', 'via': 'ae', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* This is the cleanest "merged vs parts" explanation:

  * `agent_p2` sees `ae` and moves `A → E` via `ae`.
  * `agent_p1` sees the same message but does not match its own arrow label `ab`, so it emits **STAY** on `A --[ab]--> B`.
  * `agent_p1` also runs the object handler for `"A"` (TEST trace).
* This exactly corresponds to having both arrow receive handlers present in the merged DNA (you can point to them in [`dna.json`](./dna.json): `A --[ ab ]--> B` and `A --[ ae ]--> E`).

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_5.png"/>
</p>

#### Step 6: `ec`

```
> ec
[Received] {'from': 'E', 'to': 'C', 'via': 'ec', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* Pure `agent_p2` fingerprint: `E → C` via `ec`.

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_6.png"/>
</p>

#### Step 7: `cf`

```
> cf
[Received] {'from': 'C', 'to': 'D', 'via': 'cd', 'action': 'STAY', 'agent': 'DNAMergeAgent_p1'}
[Received] {'node': 'C', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'C', 'to': 'F', 'via': 'cf', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* You typed a cycle-2 command (`cf`), so you see:

  * `agent_p2` does the real move `C → F` via `cf`.
* But because you are at **C**, `agent_p1` also has relevant structure around **C**:

  * it emits **STAY** on `C --[cd]--> D` (since you didn’t type `cd`)
  * it tests object `C`

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_7.png"/>
</p>

#### Step 8: `fa`

```
> fa
[Received] {'from': 'F', 'to': 'A', 'via': 'fa', 'action': 'MOVE', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* Pure `agent_p2` fingerprint: `F → A` via `fa`.

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_8.png"/>
</p>

### Scenario C — Non-matching input showing "fallback behavior" in both parts

#### Step 9: `hello`

```
> hello
[Received] {'node': 'A', 'action': 'TEST', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'A', 'to': 'B', 'via': 'ab', 'action': 'STAY', 'agent': 'DNAMergeAgent_p1'}
[Received] {'from': 'A', 'to': 'E', 'via': 'ae', 'action': 'STAY', 'agent': 'DNAMergeAgent_p2'}
```

What this means:

* `agent_p1` reacts to an irrelevant command by:

  * testing the current object `"A"`
  * emitting **STAY** on its `ab` arrow (because `hello != "ab"`)
* `agent_p2` reacts analogously by emitting **STAY** on its `ae` arrow (because `hello != "ae"`).

In the browser:

<p align="center">
  <img width="380" src="../../assets/img/dna_merge_whole.png"/>
</p>

> [!NOTE]
> When you want to connect any specific output line back to the composition mechanism, the easiest method is:
>
> 1. Identify the `agent` field (`DNAMergeAgent_p1` vs `DNAMergeAgent_p2`) in the log line.
> 2. Locate the corresponding `receive`/`send` entry in [`dna.json`](./dna.json) by route (for example `A --[ ab ]--> B`).
> 3. That entry points back to the original module (`"module": "agent_p1"` or `"module": "agent_p2"`), which is exactly what the merger replayed into the merged client.
