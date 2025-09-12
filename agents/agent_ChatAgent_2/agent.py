from summoner.client import SummonerClient
from multi_ainput import multi_ainput
from aioconsole import ainput, aprint
from typing import Any, Optional, Literal
import argparse, asyncio

# ---- CLI: prompt mode toggle -----------------------------------------------
# We parse the "prompt mode" early so it is available before the client starts.
# --multiline 0  -> one-line input using aioconsole.ainput("> ")
# --multiline 1  -> multi-line input using multi_ainput("> ", "~ ", "\\")
prompt_parser = argparse.ArgumentParser()
prompt_parser.add_argument("--multiline", required=False, type=int, choices=[0, 1], default=0, help="Use multi-line input mode with backslash continuation (1 = enabled, 0 = disabled). Default: 0.")
prompt_args, _ = prompt_parser.parse_known_args()

client = SummonerClient(name="ChatAgent_2")

# ---- (flow): activate the automaton/flow engine ------------------------
# Calling client.flow().activate() enables route selection based on "state".
# The engine will consult @upload_states to pick which @receive(route=...) runs.
client_flow = client.flow()
client_flow.activate()

TESTNET_HOST = "testnet.summoner.org"
TESTNET_PORT = 8888

# ---- (state): simple in-memory state driving which receive runs --------
# For this demo, `state`` is either "opened" (commands allowed) or "locked"
# (commands ignored). You toggle it with /self.open or /self.lock locally.
state: Literal["opened", "locked"] = "opened"
state_lock = asyncio.Lock()

# ---- (orchestration): upload current state to the flow engine ----------
# The value you return here is used to select matching @receive(route=...) handlers.
@client.upload_states()
async def state_orchestrator(payload: Any) -> str:
    async with state_lock:
        return state

# ---- Receive when state == "opened" ----------------------------------------
# In this state, we accept *remote* commands like /travel, /go_home, /quit.
@client.receive(route="opened")
async def receiver_opened(msg: Any) -> None:
    client.logger.info("Use handler @(route='opened')")

    # Extract content from dict payloads, or use the raw message as-is.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    # Remote commands are executed on receipt (not printed).
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

    # Not a command: print as a normal message.
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    await aprint(tag, str(content))
    await aprint(f"[opened]> ", end="")

# ---- Receive when state == "locked" ----------------------------------------
# In this state, we *ignore* remote command verbs and only display messages.
@client.receive(route="locked")
async def receiver_locked(msg: Any) -> None:
    client.logger.info("Use handler @(route='locked')")

    # Extract content from dict payloads, or use the raw message as-is.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    # Only display (no command processing here).
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    await aprint(tag, str(content))
    await aprint(f"[locked]> ", end="")

# ---- Send: available in any state ------------------------------------------
# We keep a single send handler; the prompt shows the current state and we
# expose local/self commands to toggle state or travel/quit without sending.
@client.send(route="any_state")
async def send_handler() -> Optional[str]:
    global state
    async with state_lock:
        stateful_prompt_symbol = f"[{state}]> "

    # Compose input (single-line or multi-line), with the state in the prompt.
    if bool(int(prompt_args.multiline)):
        content: str = await multi_ainput(stateful_prompt_symbol, "~ ", "\\")
    else:
        content: str = await ainput(stateful_prompt_symbol)

    # Local/self commands (consume locally; do not send a payload).
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
    elif strip == "/self.lock":
        # ---- (state control): lock the agent (commands ignored on receive)
        async with state_lock:
            state = "locked"
        return None
    elif strip == "/self.open":
        # ---- (state control): open the agent (commands processed on receive)
        async with state_lock:
            state = "opened"
        return None

    # Not a self-command: send as a normal message (e.g., '/travel' to a peer).
    return content

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
