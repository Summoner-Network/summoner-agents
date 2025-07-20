# Summoner Agent Library

## Agent formatting and desktop app compatibility

## Agent collection

### Legends

| Column          | Description                                                               |
| --------------- | ------------------------------------------------------------------------- |
| **Agent Name**  | The name or type of the agent. |
| **Description** | Short explanation of the agent’s behavior or purpose.                     |
| **Level**  | Suggested difficulty level (e.g. Beginner, Intermediate, Advanced).       |
| **Application**    | The intended application (e.g. messaging, orchestration, negotiation).    |
| **Features**    | Main summoner feature (repo) show cased through the agent code      |
| **DB**          | ✅ if the agent uses a persistent or in-memory database (`asqlite`, etc.). |
| **Queue**       | ✅ if the agent uses asynchronous queues (e.g. `asyncio.Queue`).           |
| **Flows**       | ✅ if the agent uses a modular or multi-step flow architecture.            |
| **Trigg.**    | ✅ if the agent uses triggers (event-driven mechanisms).                   |
| **Hooks**       | ✅ if the agent uses hooks for message preprocessing/postprocessing.     |
| **Temp.**    | ✅ if the agent is designed as a template to start a more complex project.       |
| **Comp.**  | ✅ if the agent is intended to integrate with other agents in a system.    |


### Core Messaging Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td> <code><strong>SendAgent0</strong> </code></td>
        <td>Demonstrate use of <code>@send</code></td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Emit-%20?color=9e2fc5"alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>✅</td><td>✅</td>
    </tr>
    <tr>
        <td><code><strong>RcvAgent0</strong></code></td>
        <td>Demonstrate use of <code>@receive</code></td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Collect-%20?color=712fc5"alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code><strong>EchoAgent0</strong></code></td>
        <td>Combine both @send and receive</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Redirect-%20?color=482fc5"alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Security & Flow-Control Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>ratelimit_0</code></td>
        <td>Trigger backpressure from server</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Red_Team-%20?color=cd280a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>hs_init_0</code></td>
        <td>Explore an initial handshake design</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Handshake-%20?color=cd710a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>hs_final_0</code></td>
        <td>Explore a final handshake design</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Handshake-%20?color=cd710a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Interaction Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code><strong>ChatAgent</strong></code></td>
        <td>Chat agent as a user interface</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Interaction-%20?color=119ace" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code><strong>CmdAgent</strong></code></td>
        <td>Chat agent to send and receve commands (user interface)</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Control-%20?color=2277f1" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Orchestration Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>reporter</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Redirect-%20?color=482fc5" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>storage</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Collect-%20?color=712fc5" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>subscribe</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Emit-%20?color=9e2fc5" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>event_emiter</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Emit-%20?color=9e2fc5" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>question</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Interaction-%20?color=119ace" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>answer</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Interaction-%20?color=119ace" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Negotiation Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>seller</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_4-%20?color=df6719" alt=""></td>
        <td><img src="https://img.shields.io/badge/Negotiation-%20?color=f1c822" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>buyer</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_4-%20?color=df6719" alt=""></td>
        <td><img src="https://img.shields.io/badge/Negotiation-%20?color=f1c822" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Connector Agents (Composability)

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>connector</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_3-%20?color=dfa018" alt=""></td>
        <td><img src="https://img.shields.io/badge/MCP-%20?color=1122ce" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### API-based Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>wikipedia</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Registry-%20?color=bd1edd" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>arxiv</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/Registry-%20?color=bd1edd" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>pubmed</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/Registry-%20?color=bd1edd" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>github</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/App-%20?color=f1227a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>reddit</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_3-%20?color=dfa018" alt=""></td>
        <td><img src="https://img.shields.io/badge/App-%20?color=f1227a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>notion</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/App-%20?color=f1227a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>slack</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_3-%20?color=dfa018" alt=""></td>
        <td><img src="https://img.shields.io/badge/App-%20?color=f1227a" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>summary (openai)</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/LLM-%20?color=18b9cc" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>parsing (openai)</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_1-%20?color=2fc56c" alt=""></td>
        <td><img src="https://img.shields.io/badge/LLM-%20?color=18b9cc" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>clustering (openai)</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/LLM-%20?color=18b9cc" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    <tr>
        <td><code>search (openai)</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_2-%20?color=b1d52d" alt=""></td>
        <td><img src="https://img.shields.io/badge/LLM-%20?color=18b9cc" alt=""></td>
        <td><code>core</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>

### Code Exchange Agents

<div style="display: flex; justify-content: center;">
<table style="border-collapse: collapse; width: 95%; text-align: center;">
    <thead>
    <tr>
        <th style="width: 10%; text-align: center;">Agent Name</th>
        <th style="width: 33%; text-align: center;">Description</th>
        <th style="width: 12%; text-align: center;">Level</th>
        <th style="width: 12%; text-align: center;">Application</th>
        <th style="width: 12%; text-align: center;">Features</th>
        <th style="width: 3%; text-align: center;">DB</th>
        <th style="width: 3%; text-align: center;">Queue</th>
        <th style="width: 3%; text-align: center;">Flows</th>
        <th style="width: 3%; text-align: center;">Trigg.</th>
        <th style="width: 3%; text-align: center;">Hooks</th>
        <th style="width: 3%; text-align: center;">Temp.</th>
        <th style="width: 3%; text-align: center;">Comp.</th>
    </tr>
    </thead>
    <tbody>
    <tr>
        <td><code>smart_tools</code></td>
        <td>...</td>
        <td><img src="https://img.shields.io/badge/LVL_3-%20?color=dfa018" alt=""></td>
        <td><img src="https://img.shields.io/badge/Smart_tools-%20?color=dd581e" alt=""></td>
        <td><code>smart-tools</code></td>
        <td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td><td>❌</td>
    </tr>
    </tbody>
</table>
</div>


<!-- 
---
reporter queue (sequence for orchestration)
storage db (GET POSt service)
subscribe (db)
event emiter
question / answer ~ pub / sub

---

seller (negotiation)
buyer (negotiation)

----

connector: connecting other framework (e..g mcp) to summoner

---

api-based
- search
- process text (summary, improve)
- live updates
- get

api-metrics

---
exchange pice of code over the wire:
smart tools -->
