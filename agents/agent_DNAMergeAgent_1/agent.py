from summoner.client import ClientMerger
import argparse, json

from agent_p1 import client as agent_1
from agent_p2 import client as agent_2

from summoner_web_viz import WebGraphVisualizer

AGENT_ID = "DNAMergeAgent_1"
viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

client = ClientMerger([
        {"var_name": "client", "client": agent_1},
        {"var_name": "client", "client": agent_2},
    ], 
    name=AGENT_ID, 
    rebind_globals={"viz": viz}
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

    from pathlib import Path
    json.dump(json.loads(client.dna(include_context=True)), (Path(__file__).resolve().parent / "dna.json").open("w", encoding="utf-8"), indent=2, ensure_ascii=False)

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")
