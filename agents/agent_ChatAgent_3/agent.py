from summoner.client import SummonerClient
from summoner.protocol import Move, Stay, Node, Event
from multi_ainput import multi_ainput
from aioconsole import ainput, aprint
from typing import Any, Literal, Optional
import argparse, asyncio

# ---- CLI: prompt mode toggle -----------------------------------------------
# We parse the "prompt mode" early so it is available before the client starts.
# --multiline 0  -> one-line input using aioconsole.ainput("> ")
# --multiline 1  -> multi-line input using multi_ainput("> ", "~ ", "\\")
prompt_parser = argparse.ArgumentParser()
prompt_parser.add_argument("--multiline", required=False, type=int, choices=[0, 1], default=0, help="Use multi-line input mode with backslash continuation (1 = enabled, 0 = disabled). Default: 0.")
prompt_args, _ = prompt_parser.parse_known_args()

client = SummonerClient(name="ChatAgent_3")

# ---- Activate the automaton/flow engine --------------------------------
# The flow engine orchestrates which @receive(route=...) runs based on state
# and handler return values (Move/Stay). We also declare how to parse route
# strings like "opened --> locked" via a custom arrow style, then 'ready()'
# compiles internal regex/patterns.
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
client_flow.ready()

# ---- Triggers ---------------------------------------------------------------
# Triggers are loaded dynamically (from TRIGGERS file). For this demo, 'ok' exists.
Trigger = client_flow.triggers()

TESTNET_HOST = "testnet.summoner.org"
TESTNET_PORT = 8888

# ---- State (drives which receiver is active) --------------------------------
# Two states: "opened" (execute remote commands) vs "locked" (do NOT execute).
state: Literal["opened", "locked"] = "opened"
state_lock = asyncio.Lock()

# ---- Upload current state to the flow engine --------------------------------
# The flow engine calls this to know our current state; that state is matched
# against @receive(route=...) definitions.
@client.upload_states()
async def state_orchestrator(payload: Any) -> str:
    async with state_lock:
        return state

# ---- Integrate possible next states (from handlers) -------------------------
# After handlers return Move/Stay, the engine aggregates "possible" states and
# provides them here. We fold that set back into our local `state`.
@client.download_states()
async def state_processor(possible_states: list[Node]) -> None:
    global state
    async with state_lock:
        state = "opened" if any(n == Node("opened") for n in possible_states) else "locked"

# ---- Receive: opened --> locked ---------------------------------------------
# Route expresses a *transition capability*: when we're in "opened", this handler
# runs; it can return Move(Trigger.ok) to transition to "locked", or Stay(Trigger.ok)
# to remain in "opened".
@client.receive(route="opened --> locked")
async def receiver_opened(msg: Any) -> Event:
    # Normalize inbound content.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    if isinstance(content, str):
        strip = content.strip()
        # Remote commands executed here (as in ChatAgent_1), but now we return flow Events.
        if strip == "/travel":
            await client.travel_to(host=TESTNET_HOST, port=TESTNET_PORT)
            return Stay(Trigger.ok)
        elif strip == "/go_home":
            await client.travel_to(host=client.default_host, port=client.default_port)
            return Stay(Trigger.ok)
        elif strip == "/quit":
            await client.quit()
            return Stay(Trigger.ok)
        elif strip == "/lock":
            # allow remote command to *lock* this agent
            tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
            await aprint(tag, str(content))
            await aprint(f"[locked]> ", end="")
            return Move(Trigger.ok)  # transition "opened" -> "locked"

    # Not a command: print and stay in the same state.
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    await aprint(tag, str(content))
    await aprint(f"[opened]> ", end="")
    return Stay(Trigger.ok)

# ---- Receive: locked --> opened ---------------------------------------------
# When we're "locked", this handler runs; it does NOT execute travel/quit/etc.
# Instead, it can *unlock* on a specific command (/open <pw>), or Stay locked.
@client.receive(route="locked --> opened")
async def receiver_locked(msg: Any) -> Event:
    # Normalize inbound content.
    content = (msg["content"] if isinstance(msg, dict) and "content" in msg else msg)

    if isinstance(content, str):
        # Minimal parse for '/open <password>'.
        try:
            parts = content.strip().split(maxsplit=1)  # split on any whitespace once
            if len(parts) == 2:
                cmd, pw = parts[0], parts[1].strip()
                if cmd == "/open" and pw == "HelloSummoner":
                    # remote-controlled unlock
                    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
                    await aprint(tag, str(content))
                    await aprint(f"[opened]> ", end="")
                    return Move(Trigger.ok)  # transition "locked" -> "opened"
        except Exception:
            pass  # fall through to print+stay

    # In 'locked', we only display messages; commands like '/travel' are shown, not executed.
    tag = ("\r[From server]" if isinstance(content, str) and content[:len("Warning:")] == "Warning:" else "\r[Received]")
    await aprint(tag, str(content))
    await aprint(f"[locked]> ", end="")
    return Stay(Trigger.ok)

# ---- Send: available in any state -------------------------------------------
# Prompt shows the current state. Local '/self.*' commands act immediately and
# do not send payloads (same pattern as ChatAgent_1/2).
@client.send(route="any_state")
async def send_handler() -> Optional[str]:
    global state
    async with state_lock:
        stateful_prompt_symbol = f"[{state}]> "

    # Compose (one- or multi-line).
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
        async with state_lock:
            state = "locked"
        return None
    elif strip == "/self.open":
        async with state_lock:
            state = "opened"
        return None

    # Otherwise, send as a normal message (it can be a remote command for the peer).
    return content

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
