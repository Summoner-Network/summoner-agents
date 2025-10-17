from summoner.client import SummonerClient
from multi_ainput import multi_ainput
from aioconsole import ainput, aprint
from typing import Any
import argparse, json

# ---- CLI: prompt mode toggle -----------------------------------------------
# We parse the "prompt mode" early so it is available before the client starts.
# --multiline 0  -> one-line input using aioconsole.ainput("> ")
# --multiline 1  -> multi-line input using multi_ainput("> ", "~ ", "\\")
prompt_parser = argparse.ArgumentParser()
prompt_parser.add_argument("--multiline", required=False, type=int, choices=[0, 1], default=0, help="Use multi-line input mode with backslash continuation (1 = enabled, 0 = disabled). Default: 0.")
prompt_args, _ = prompt_parser.parse_known_args()

client = SummonerClient(name="InputAgent")

@client.receive(route="")
async def receiver_handler(msg: Any) -> None:
    # Extract content from dict payloads, or use the raw message as-is.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    # Choose a display tag. This is visual only; it does not affect routing.
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")

    # Print the message and then re-show the primary prompt marker.
    await aprint(tag, str(content))
    await aprint("> ", end="")

@client.send(route="")
async def send_handler() -> str:
    if bool(int(prompt_args.multiline)):
        # Multi-line compose with continuation and echo cleanup.
        content: str = await multi_ainput("> ", "~ ", "\\")
    else:
        # Single-line compose.
        content: str = await ainput("> ")

    # Parse as JSON if possible; otherwise, return the raw string
    output = None
    try:
        output = json.loads(content.replace("\n", ""))
    except:
        output = content
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
