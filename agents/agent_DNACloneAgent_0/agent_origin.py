from summoner.client import SummonerClient
from summoner.protocol import Node, Move, Stay, Test, Action, Event
import argparse, json
from typing import Any, Union

from summoner_web_viz import WebGraphVisualizer

AGENT_ID = "DNACloneAgent_origin"
viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

client = SummonerClient(name=AGENT_ID)
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

# This agent only uses A,B,C,D, but we implement state handling for A..F
# so it composes cleanly under merge.
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


# ---- Arrow receives (cycle 1) ----

@client.receive(route=" A --[ ab ]--> B ")
async def ab(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "ab" else Stay(Trigger.ok)

@client.receive(route=" B --[ bc ]--> C ")
async def bc(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "bc" else Stay(Trigger.ok)

@client.receive(route=" C --[ cd ]--> D ")
async def cd(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "cd" else Stay(Trigger.ok)

@client.receive(route=" D --[ da ]--> A ")
async def da(msg: Union[str, dict]) -> Event:
    cmd = _content(msg)
    return Move(Trigger.ok) if cmd == "da" else Stay(Trigger.ok)

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

# ---- Object receives (optional, but shows object handlers) ----

@client.receive(route="A")
async def obj_A(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="B")
async def obj_B(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="C")
async def obj_C(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="D")
async def obj_D(_: Any) -> Event:
    return Test(Trigger.ok)


# ---- Sends: trace moves/stays/tests ----

@client.send(route="A--[ab]-->B", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_ab_move() -> dict:
    return {"from": "A", "to": "B", "via": "ab", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="B--[bc]-->C", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_bc_move() -> dict:
    return {"from": "B", "to": "C", "via": "bc", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="C--[cd]-->D", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_cd_move() -> dict:
    return {"from": "C", "to": "D", "via": "cd", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="D--[da]-->A", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_da_move() -> dict:
    return {"from": "D", "to": "A", "via": "da", "action": "MOVE", "agent": AGENT_ID}

@client.send(route="A--[ab]-->B", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_ab_stay() -> dict:
    return {"from": "A", "to": "B", "via": "ab", "action": "STAY", "agent": AGENT_ID}

@client.send(route="B--[bc]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_bc_stay() -> dict:
    return {"from": "B", "to": "C", "via": "bc", "action": "STAY", "agent": AGENT_ID}

@client.send(route="C--[cd]-->D", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_cd_stay() -> dict:
    return {"from": "C", "to": "D", "via": "cd", "action": "STAY", "agent": AGENT_ID}

@client.send(route="D--[da]-->A", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_da_stay() -> dict:
    return {"from": "D", "to": "A", "via": "da", "action": "STAY", "agent": AGENT_ID}

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
async def send_ae_stay() -> dict:
    return {"from": "A", "to": "E", "via": "ae", "action": "STAY", "agent": AGENT_ID}

@client.send(route="E--[ec]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_ec_stay() -> dict:
    return {"from": "E", "to": "C", "via": "ec", "action": "STAY", "agent": AGENT_ID}

@client.send(route="C--[cf]-->F", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_cf_stay() -> dict:
    return {"from": "C", "to": "F", "via": "cf", "action": "STAY", "agent": AGENT_ID}

@client.send(route="F--[fa]-->A", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_fa_stay() -> dict:
    return {"from": "F", "to": "A", "via": "fa", "action": "STAY", "agent": AGENT_ID}


@client.send(route="A", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_A_test() -> dict:
    return {"node": "A", "action": "TEST", "agent": AGENT_ID}

@client.send(route="B", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_B_test() -> dict:
    return {"node": "B", "action": "TEST", "agent": AGENT_ID}

@client.send(route="C", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_C_test() -> dict:
    return {"node": "C", "action": "TEST", "agent": AGENT_ID}

@client.send(route="D", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_D_test() -> dict:
    return {"node": "D", "action": "TEST", "agent": AGENT_ID}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()  # optional, but harmless
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states([Node(state)])

    from pathlib import Path
    json.dump(json.loads(client.dna(include_context=True)), (Path(__file__).resolve().parent / "agent_origin_dna.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
