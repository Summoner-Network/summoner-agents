import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")
import argparse, json, asyncio
from typing import Any, Optional
from aioconsole import aprint

from summoner.client import SummonerClient
from summoner.protocol import Direction, Node, Event, Stay, Move, Action, Test

from llm_call import LLMClient
from summoner_web_viz import WebGraphVisualizer

# -----------------------------------------------------------------------------
# Minimal config
# -----------------------------------------------------------------------------
AGENT_ID = "CatArrowAgent"

# Turn on debug if you want to see the full prompt printed by LLMClient
llm_client = LLMClient(debug=True)

viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)


states = [Node("A")]
state_lock = asyncio.Lock()

# -----------------------------------------------------------------------------
# Summoner client + flow
# -----------------------------------------------------------------------------
client = SummonerClient(name=AGENT_ID)
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

# -----------------------------------------------------------------------------
# State upload/download (minimal)
# -----------------------------------------------------------------------------
@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    global states
    async with state_lock:
        if not states:
            await aprint("Going back to state 'A' due to missing states")
            states = [Node("A")]
        viz.push_states(states)
        return states

@client.download_states()
async def state_processor(possible_states: list[Node]) -> None:
    global states
    async with state_lock:
        if possible_states:
            states = possible_states
        else:
            states = [Node("A")]
    viz.push_states(states)

# -----------------------------------------------------------------------------
# Hooks (minimal)
# -----------------------------------------------------------------------------
@client.hook(direction=Direction.RECEIVE)
async def validate_incoming(msg: Any) -> Optional[dict]:
    """
    Expect:
      msg = {"remote_addr": "...", "content": {...}}
    """
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        return None
    return msg


@client.hook(direction=Direction.SEND)
async def add_sender_id(payload: Any) -> Optional[dict]:
    """
    Normalize outgoing payload to a dict and attach a stable sender id.
    """
    if isinstance(payload, str):
        payload = {"message": payload}
    if not isinstance(payload, dict):
        return None
    payload["from"] = AGENT_ID
    return payload


# -----------------------------------------------------------------------------
# Receive handlers
# -----------------------------------------------------------------------------
@client.receive(route=" A --[ f ]--> B ")
async def arrow_f_A_B(msg: Any) -> Event:
    pa = client_flow.parse_route(" A --[ f ]--> B ")
    answers = await llm_client.run(
        incoming=msg["content"],
        actions=("move", "stay"),
        context={
            "route": str(pa), 
            "source": str(next(iter(pa.source))), 
            "arrow": str(next(iter(pa.label))), 
            "target": str(next(iter(pa.target))),
        },
        intro=f"You are {AGENT_ID}. Current position: ARROW f: A -> B.",
    )
    await aprint(f"\033[34m{json.dumps(answers, indent=2)}\033[0m")

    if answers.get("action") == "move":
        return Move(Trigger.ok)
    elif answers.get("action") == "stay":
        return Stay(Trigger.ok)
    else:
        return None


@client.receive(route="A")
async def object_A(_: Any) -> Event:
    return Test(Trigger.ok)


@client.receive(route="B")
async def object_B(_: Any) -> Event:
    return Test(Trigger.ok)


@client.receive(route="f")
async def cell_f(_: Any) -> Event:
    return Test(Trigger.ok)


# -----------------------------------------------------------------------------
# Send handler: Event trace sent to the server
# -----------------------------------------------------------------------------
@client.send(route="A--[f]-->B", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move() -> str:
    return "Decided to move from A to B via f"

@client.send(route="A--[f]-->B", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay() -> str:
    return "Decided to stay on A and not traverse f"

@client.send(route="A", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_A() -> str:
    return "A processed and forgotten"

@client.send(route="B", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_B() -> str:
    return "B processed and forgotten"

@client.send(route="f", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_f() -> str:
    return "f processed and forgotten"

# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()  # optional, but harmless
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(states)

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")