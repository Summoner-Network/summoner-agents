from summoner.client import SummonerClient
from multi_ainput import multi_ainput
from aioconsole import ainput
from typing import Any, Optional
import argparse

# ---- CLI: prompt mode toggle -----------------------------------------------
# We parse the "prompt mode" early so it is available before the client starts.
# --multiline 0  -> one-line input using aioconsole.ainput("> ")
# --multiline 1  -> multi-line input using multi_ainput("> ", "~ ", "\\")
prompt_parser = argparse.ArgumentParser()
prompt_parser.add_argument("--multiline", required=False, type=int, choices=[0, 1], default=0, help="Use multi-line input mode with backslash continuation (1 = enabled, 0 = disabled). Default: 0.")
prompt_args, _ = prompt_parser.parse_known_args()

client = SummonerClient(name="ChatAgent_1")

TESTNET_HOST = "testnet.summoner.org"
TESTNET_PORT = 8888

@client.receive(route="")
async def receiver_handler(msg: Any) -> None:
    """
    Inbound path:
      - Normalize `msg` to a content string (dicts may carry {"content": ...}).
      - Execute remote commands when the *other* side asks us to do something.
      - Otherwise, print a tag and re-show the primary prompt.
    Remote commands (coming from another agent):
      /travel   -> travel to testnet
      /go_home  -> travel back to our default host/port
      /quit     -> terminate this client
    """
    # Extract content from dict payloads, or use the raw message as-is.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    # If the content is a string, check for remote commands we should execute.
    if isinstance(content, str):
        strip = content.strip()
        if strip == "/travel":
            await client.travel_to(host=TESTNET_HOST, port=TESTNET_PORT)
            return
        elif strip == "/go_home":
            await client.travel_to(host=client.default_host, port=client.default_port)
            return
        elif strip == "/quit":
            await client.quit()
            return

    # Not a command: choose a display tag and print as a normal message.
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    print(tag, content, flush=True)
    print("> ", end="", flush=True)

@client.send(route="")
async def send_handler() -> Optional[str]:
    """
    Outbound path (prompt-driven):
      - If --multiline=1: use multi_ainput("> ", "~ ", "\\") for continuation.
      - If --multiline=0: single ainput("> ") line.
      - Handle *local/self* commands typed by the user:
          /self.travel  -> travel to testnet
          /self.go_home -> travel back to default host/port
          /self.quit    -> terminate this client
      - Otherwise, send the typed content to the server as-is. This allows
        sending '/travel' or '/quit' to trigger actions on a *remote* ChatAgent_1.
    """
    if bool(int(prompt_args.multiline)):
        # Multi-line compose with continuation and echo cleanup.
        content: str = await multi_ainput("> ", "~ ", "\\")
    else:
        # Single-line compose.
        content: str = await ainput("> ")

    # Local/self commands (do not send a payload; perform the action).
    strip = content.strip()
    if strip == "/self.travel":
        await client.travel_to(host=TESTNET_HOST, port=TESTNET_PORT)
        return None
    elif strip == "/self.go_home":
        await client.travel_to(host=client.default_host, port=client.default_port)
        return None
    elif strip == "/self.quit":
        await client.quit()
        return None

    # Not a self-command: send as a normal message (e.g., '/travel' to a peer).
    return content

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
