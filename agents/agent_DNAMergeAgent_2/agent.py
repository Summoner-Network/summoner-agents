from summoner.client import ClientMerger
import argparse, json
from summoner.protocol import Node, Event

from summoner_web_viz import WebGraphVisualizer
from pathlib import Path

AGENT_ID = "DNAMergeAgent_2"
viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

from summoner.protocol.triggers import load_triggers
Trigger = load_triggers()

OBJECTS = {Node(x) for x in ["A","B","C","D","E","F"]}

def _content(msg):
    return msg.get("content") if isinstance(msg, dict) else msg

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
        }
    )

client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
client.initiate_all()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    # Start visual window (browser) and build graph from dna
    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()  # optional, but harmless
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states([])

    json.dump(json.loads(client.dna(include_context=True)), (Path(__file__).resolve().parent / "dna.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
