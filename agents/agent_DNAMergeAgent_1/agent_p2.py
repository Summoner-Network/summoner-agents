from summoner.client import SummonerClient
from summoner.protocol import Node, Move, Stay, Test, Action, Event
import argparse, json
from typing import Any, Union

from summoner_web_viz import WebGraphVisualizer

AGENT_ID = "DNAMergeAgent_p2"
viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

client = SummonerClient(name=AGENT_ID)
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

OBJECTS = {Node(x) for x in ["A", "B", "C", "D", "E", "F"]}
state = "A"

def _content(msg: Any) -> Any:
    return msg.get("content") if isinstance(msg, dict) else msg

@client.upload_states()
async def upload_states(_: Any) -> Node:
    global state
    viz.push_states([Node(state)])
    return Node(state)

@client.download_states()
async def download_states(possible_states: list[Node]) -> None:
    global state
    ps = set(possible_states or [])
    arrows = [n for n in ps if n not in OBJECTS]
    candidates = sorted([n for n in ps if n in OBJECTS], key=lambda n: str(n))
    if not candidates:
        return
    if Node(state) in candidates and len(candidates) > 1:
        candidates = [n for n in candidates if n != Node(state)]
    state = str(candidates[0])
    viz.push_states([Node(state)] + arrows)


# ---- Arrow receives (cycle 2) ----

@client.receive(route=" A --[ ae ]--> E ")
async def ae(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "ae" else Stay(Trigger.ok)

@client.receive(route=" E --[ ec ]--> C ")
async def ec(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "ec" else Stay(Trigger.ok)

@client.receive(route=" C --[ cf ]--> F ")
async def cf(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "cf" else Stay(Trigger.ok)

@client.receive(route=" F --[ fa ]--> A ")
async def fa(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "fa" else Stay(Trigger.ok)

# ---- Object receives (optional) ----

@client.receive(route="A")
async def obj_A(_: Any) -> Any:
    return Test(Trigger.ok)

@client.receive(route="E")
async def obj_E(_: Any) -> Any:
    return Test(Trigger.ok)

@client.receive(route="C")
async def obj_C(_: Any) -> Any:
    return Test(Trigger.ok)

@client.receive(route="F")
async def obj_F(_: Any) -> Any:
    return Test(Trigger.ok)

# ---- Sends ----

@client.send(route="A--[ae]-->E", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_ae_move() -> dict:
    return {"from": "A", "to": "E", "via": "ae", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="E--[ec]-->C", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_ec_move() -> dict:
    return {"from": "E", "to": "C", "via": "ec", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="C--[cf]-->F", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_cf_move() -> dict:
    return {"from": "C", "to": "F", "via": "cf", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="F--[fa]-->A", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_fa_move() -> dict:
    return {"from": "F", "to": "A", "via": "fa", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="A--[ae]-->E", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_ae_move() -> dict:
    return {"from": "A", "to": "E", "via": "ae", "action": "STAY", "agent": AGENT_ID}

@client.send(route="E--[ec]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_ec_move() -> dict:
    return {"from": "E", "to": "C", "via": "ec", "action": "STAY", "agent": AGENT_ID}

@client.send(route="C--[cf]-->F", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_cf_move() -> dict:
    return {"from": "C", "to": "F", "via": "cf", "action": "STAY", "agent": AGENT_ID}

@client.send(route="F--[fa]-->A", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_fa_move() -> dict:
    return {"from": "F", "to": "A", "via": "fa", "action": "STAY", "agent": AGENT_ID}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()  # optional, but harmless
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states([Node(state)])

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
