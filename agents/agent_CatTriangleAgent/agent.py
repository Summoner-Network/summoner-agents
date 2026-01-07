import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import argparse, json, asyncio, re
from typing import Any, Optional, Dict, List
from aioconsole import aprint

from summoner.client import SummonerClient
from summoner.protocol import Direction, Node, Event, Stay, Move, Action, Test

from llm_call import LLMClient
from summoner_web_viz import WebGraphVisualizer

# -----------------------------------------------------------------------------
# Minimal config
# -----------------------------------------------------------------------------
AGENT_ID = "CatTriangleAgent"
llm_client = LLMClient(debug=True)

viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

REQUIRED_FIELDS = ["first_name", "last_name", "company", "email"]

# Uncommitted scratchpad in A
draft: Dict[str, Any] = {}

# Committed registration in B
registration: Dict[str, Any] = {}

mem_lock = asyncio.Lock()

states = [Node("A")]
state_lock = asyncio.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _is_complete(reg: Dict[str, Any]) -> bool:
    for k in REQUIRED_FIELDS:
        v = reg.get(k)
        if v is None:
            return False
        if isinstance(v, str) and not v.strip():
            return False
    return True

def _missing_fields(reg: Dict[str, Any]) -> List[str]:
    miss: List[str] = []
    for k in REQUIRED_FIELDS:
        v = reg.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            miss.append(k)
    return miss

def _has_names(d: Dict[str, Any]) -> bool:
    fn = d.get("first_name")
    ln = d.get("last_name")
    return isinstance(fn, str) and fn.strip() != "" and isinstance(ln, str) and ln.strip() != ""

def _has_email_or_company(d: Dict[str, Any]) -> bool:
    email = d.get("email")
    company = d.get("company")
    ok_email = isinstance(email, str) and email.strip() != ""
    ok_company = isinstance(company, str) and company.strip() != ""
    return ok_email or ok_company

def _clean_extracted(x: Dict[str, Any]) -> Dict[str, Any]:
    """
    - drop empty strings
    - basic email validation
    - tiny normalization: email lowercased; names/company trimmed
    """
    out: Dict[str, Any] = {}
    for k in REQUIRED_FIELDS:
        if k not in x:
            continue
        v = x.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue

        if k == "email":
            if not isinstance(v, str):
                continue
            v2 = v.strip().lower()
            if not _EMAIL_RE.match(v2):
                continue
            out[k] = v2

        elif k in ("first_name", "last_name"):
            # minimal normalization: Title-case words, but keep it simple
            if isinstance(v, str):
                out[k] = v.strip().split()[0].capitalize() if v.strip() else v
            else:
                out[k] = v

        elif k == "company":
            out[k] = v.strip() if isinstance(v, str) else v

        else:
            out[k] = v

    return out

def _merge(dst: Dict[str, Any], src: Dict[str, Any], *, protect_keys: Optional[set[str]] = None) -> None:
    """
    Merge src into dst.
    If protect_keys is provided, keys already present in dst will not be overwritten.
    """
    protect_keys = protect_keys or set()
    for k, v in src.items():
        if k in protect_keys and k in dst and isinstance(dst.get(k), str) and dst[k].strip() != "":
            continue
        dst[k] = v

# -----------------------------------------------------------------------------
# Summoner client + flow
# -----------------------------------------------------------------------------
client = SummonerClient(name=AGENT_ID)
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()


# -----------------------------------------------------------------------------
# State upload/download (priority + resume B if commit exists)
# -----------------------------------------------------------------------------
@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    global states
    async with state_lock:
        if not states:
            async with mem_lock:
                states = [Node("B")] if (registration and not _is_complete(registration)) else [Node("A")]
        viz.push_states(states)
        return states


@client.download_states()
async def state_processor(possible_states: list[Node]) -> None:
    """
    Priority: C > B > A
    On C, clear both draft and registration.
    """
    global states
    ps = list(set(possible_states))

    if Node("C") in ps:
        kept = [x for x in ps if x in [Node("C"), Node("g"), Node("h")]]
        if Node("C") not in kept:
            kept = [Node("C")]
        async with state_lock:
            states = kept
        viz.push_states(states)

        async with mem_lock:
            draft.clear()
            registration.clear()

        return

    if Node("B") in ps:
        kept = [x for x in ps if x in [Node("B"), Node("f"), Node("g")]]
        if Node("B") not in kept:
            kept = [Node("B")]
        async with state_lock:
            states = kept
        viz.push_states(states)
        return

    kept = [x for x in ps if x in [Node("A"), Node("f"), Node("h")]]
    if Node("A") not in kept:
        kept = [Node("A")]
    async with state_lock:
        states = kept
    viz.push_states(states)


# -----------------------------------------------------------------------------
# Hooks
# -----------------------------------------------------------------------------
@client.hook(direction=Direction.RECEIVE)
async def validate_incoming(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        return None
    return msg


@client.hook(direction=Direction.SEND)
async def add_sender_id(payload: Any) -> Optional[dict]:
    if isinstance(payload, str):
        payload = {"message": payload}
    if not isinstance(payload, dict):
        return None
    payload["from"] = AGENT_ID
    return payload


# -----------------------------------------------------------------------------
# Receives (triangle): LLM is extraction only
# -----------------------------------------------------------------------------
@client.receive(route=" A --[ f ]--> B ")
async def arrow_f_A_B(msg: Any) -> Event:
    async with mem_lock:
        snap = dict(draft)

    context = {
        "required_fields": list(REQUIRED_FIELDS),
        "draft_so_far": snap,
        "missing_fields": _missing_fields(snap),
    }

    extracted = await llm_client.extract(
        incoming=msg["content"],
        allowed_fields=REQUIRED_FIELDS,
        intro=f"You are {AGENT_ID}, and helpful information extractor.",
        context=context,
    )
    extracted = _clean_extracted(extracted)
    await aprint(f"\033[34m[f] extracted: {json.dumps(extracted, indent=2)}\033[0m")

    async with mem_lock:
        _merge(draft, extracted)
        cand = dict(draft)

        if not (_has_names(cand) and _has_email_or_company(cand)):
            return Stay(Trigger.ok)

        if _is_complete(cand):
            return Stay(Trigger.ok)

        registration.clear()
        registration.update(cand)
        draft.clear()

        return Move(Trigger.ok)


@client.receive(route=" B --[ g ]--> C ")
async def arrow_g_B_C(msg: Any) -> Event:
    async with mem_lock:
        snap = dict(registration)

    context = {
        "required_fields": list(REQUIRED_FIELDS),
        "registration_so_far": snap,
        "missing_fields": _missing_fields(snap),
    }

    extracted = await llm_client.extract(
        incoming=msg["content"],
        allowed_fields=REQUIRED_FIELDS,
        intro=f"You are {AGENT_ID}, and helpful information extractor.",
        context=context,
    )
    extracted = _clean_extracted(extracted)
    await aprint(f"\033[34m[g] extracted: {json.dumps(extracted, indent=2)}\033[0m")

    async with mem_lock:
        _merge(registration, extracted, protect_keys={"first_name", "last_name"})
        return Move(Trigger.ok) if _is_complete(registration) else Stay(Trigger.ok)


@client.receive(route=" A --[ h ]--> C ")
async def arrow_h_A_C(msg: Any) -> Event:
    async with mem_lock:
        snap = dict(draft)

    context = {
        "required_fields": list(REQUIRED_FIELDS),
        "draft_so_far": snap,
        "missing_fields": _missing_fields(snap),
    }

    extracted = await llm_client.extract(
        incoming=msg["content"],
        allowed_fields=REQUIRED_FIELDS,
        intro=f"You are {AGENT_ID}, and helpful information extractor.",
        context=context,
    )
    extracted = _clean_extracted(extracted)
    await aprint(f"\033[34m[h] extracted: {json.dumps(extracted, indent=2)}\033[0m")

    async with mem_lock:
        _merge(draft, extracted)
        return Move(Trigger.ok) if _is_complete(draft) else Stay(Trigger.ok)


# -----------------------------------------------------------------------------
# Objects and cells (minimal)
# -----------------------------------------------------------------------------
@client.receive(route="A")
async def object_A(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="B")
async def object_B(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="C")
async def object_C(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="f")
async def cell_f(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="g")
async def cell_g(_: Any) -> Event:
    return Test(Trigger.ok)

@client.receive(route="h")
async def cell_h(_: Any) -> Event:
    return Test(Trigger.ok)


# -----------------------------------------------------------------------------
# Send handlers
# -----------------------------------------------------------------------------
@client.send(route="A--[f]-->B", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_f() -> str:
    return "Moved A -> B via f (commit started)"

@client.send(route="A--[f]-->B", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_f() -> str:
    return "Stayed on A via f (no commit)"

@client.send(route="B--[g]-->C", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_g() -> str:
    return "Moved B -> C via g (commit finished)"

@client.send(route="B--[g]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_g() -> str:
    return "Stayed on B via g (commit incomplete)"

@client.send(route="A--[h]-->C", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_h() -> str:
    return "Moved A -> C via h (direct success)"

@client.send(route="A--[h]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_h() -> str:
    return "Stayed on A via h (not complete)"

@client.send(route="A", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_A() -> str:
    return "A processed and forgotten"

@client.send(route="B", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_B() -> str:
    return "B processed and forgotten"

@client.send(route="C", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_C() -> str:
    return "C processed and forgotten"

@client.send(route="f", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_f() -> str:
    return "f processed and forgotten"

@client.send(route="g", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_g() -> str:
    return "g processed and forgotten"

@client.send(route="h", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_h() -> str:
    return "h processed and forgotten"


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path")
    args = parser.parse_args()

    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(states)

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
