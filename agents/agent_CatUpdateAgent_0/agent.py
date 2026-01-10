import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import argparse, json, asyncio, re
from typing import Any, Optional, Dict, List, Sequence
from aioconsole import aprint

from summoner.client import SummonerClient
from summoner.protocol import Direction, Node, Event, Stay, Move, Action, Test

from llm_call import LLMClient
from summoner_web_viz import WebGraphVisualizer

# -----------------------------------------------------------------------------
# Minimal config
# -----------------------------------------------------------------------------
AGENT_ID = "CatUpdateAgent_0"
llm_client = LLMClient(debug=True)
viz = WebGraphVisualizer(title=f"{AGENT_ID} Graph", port=8765)

# -----------------------------------------------------------------------------
# Graph tokens (objects, 1-cells, 2-cells)
# -----------------------------------------------------------------------------
# Routes (1-level)
ROUTE_F = " A --[ f ]--> B "
ROUTE_G = " A --[ g ]--> B "
ROUTE_H = " B --[ h ]--> C "
ROUTE_P = " C --[ p ]--> D "
ROUTE_Q = " C --[ q ]--> D "

# Routes (2-level, "transitions between transitions")
ROUTE_ETA_F = " f --[ eta_f ]--> p "
ROUTE_MU_F  = " f --[ mu_f  ]--> q "
ROUTE_ETA_G = " g --[ eta_g ]--> p "
ROUTE_MU_G  = " g --[ mu_g  ]--> q "

# -----------------------------------------------------------------------------
# Field schemas (used for gating + extraction)
# -----------------------------------------------------------------------------
INCIDENT_KEYS = {
    "part_id", "qty", "required_by_hours", "line_down", "dc_on_hand", "supplier_lead_time_hours"
}
POLICY_KEYS = {
    "expedite_budget_usd", "downtime_cost_per_hour_usd", "disruption_risk", "carrier_capacity_score"
}
OPS_KEYS = {
    "ready_to_tender", "pick_pack_complete", "compliance_cleared"
}
FEAS_KEYS = {
    "premium_mode_available", "economy_mode_available", "next_milk_run_departure_hours"
}

# -----------------------------------------------------------------------------
# Memory (path-dependent)
# -----------------------------------------------------------------------------
mem_lock = asyncio.Lock()

case: Dict[str, Any] = {}
policy: Dict[str, Any] = {}
ops: Dict[str, Any] = {}
feas: Dict[str, Any] = {}

history: Dict[str, Any] = {
    "stage": "A",               # "A" | "B" | "C" | "D"
    "source_choice": None,      # "f" | "g"
    "amendment_choice": None,   # "eta_f" | "mu_f" | "eta_g" | "mu_g"
    "intent_lane": None,        # "p" | "q"  (set by 2-cell)
    "shipment_lane": None,      # "p" | "q"  (set at C->D)
    "exception": None,          # override explanation
    "evaluation": None,         # stored at D
}
report_emitted = False

def _reset_all() -> None:
    case.clear()
    policy.clear()
    ops.clear()
    feas.clear()
    history.update(
        stage="A",
        source_choice=None,
        amendment_choice=None,
        intent_lane=None,
        shipment_lane=None,
        exception=None,
        evaluation=None,
    )
    global report_emitted
    report_emitted = False

# -----------------------------------------------------------------------------
# Helpers: coercion
# -----------------------------------------------------------------------------
def _coerce_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "yes", "y", "1"}:
            return True
        if s in {"false", "no", "n", "0"}:
            return False
    return None

def _coerce_int(v: Any) -> Optional[int]:
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(float(s))
        except Exception:
            return None
    return None

def _coerce_float(x: Any) -> Optional[float]:
    """
    Best-effort float coercion:
    - accepts int/float
    - accepts strings like "$1,200", "$120/hour", "5k", "0.8 (decent)", "80%"
    - does NOT parse number words like "five thousand" (LLM should output 5000)
    """
    if x is None:
        return None
    if isinstance(x, (int, float)):
        # Reject NaN, inf if you want, but keep simple for now.
        return float(x)

    if not isinstance(x, str):
        return None

    s = x.strip().lower()
    if s == "":
        return None

    # Handle percent: "80%" -> 0.8
    if "%" in s:
        m = _NUM_RE.search(s)
        if not m:
            return None
        try:
            return float(m.group(0)) / 100.0
        except Exception:
            return None

    # Normalize typical noise
    s = s.replace(",", "")
    # Find the first numeric substring
    m = _NUM_RE.search(s)
    if not m:
        return None

    try:
        val = float(m.group(0))
    except Exception:
        return None

    # Look for a magnitude suffix near the matched number (k/m/b)
    # Examples: "5k", "5k/hour", "$5k per hour"
    suffix_region = s[m.end(): m.end() + 4]  # small window after number
    if "k" in suffix_region:
        val *= 1e3
    elif "m" in suffix_region:
        val *= 1e6
    elif "b" in suffix_region:
        val *= 1e9

    return val

def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        dst[k] = v

def _subset_dict(payload: Any, allowed: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {k: payload[k] for k in allowed if k in payload}

# -----------------------------------------------------------------------------
# Deterministic incident parsing for robust demo (no LLM needed for your example)
# -----------------------------------------------------------------------------
_PART_RE = re.compile(r"\b([A-Za-z]{1,5}-\d{2,})\b")
_QTY_RE = re.compile(r"\bqty\s*[:=]?\s*(\d+)\b", re.IGNORECASE)
_WITHIN_H_RE = re.compile(r"\bwithin\s*(\d+(?:\.\d+)?)\s*hours?\b", re.IGNORECASE)
_DC_ON_HAND_RE = re.compile(r"\bdc\b.*?\b(\d+)\b.*?\bon\s+hand\b", re.IGNORECASE)
_SUP_LT_RE = re.compile(r"\blead\s*time\b.*?(\d+(?:\.\d+)?)\s*h\b", re.IGNORECASE)
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

def _parse_incident_text(s: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    t = s.strip()

    m = _PART_RE.search(t)
    if m:
        out["part_id"] = m.group(1)

    m = _QTY_RE.search(t)
    if m:
        out["qty"] = int(m.group(1))

    m = _WITHIN_H_RE.search(t)
    if m:
        out["required_by_hours"] = float(m.group(1))

    m = _DC_ON_HAND_RE.search(t)
    if m:
        out["dc_on_hand"] = int(m.group(1))

    m = _SUP_LT_RE.search(t)
    if m:
        out["supplier_lead_time_hours"] = float(m.group(1))

    if "line-down" in t.lower() or "line down" in t.lower() or "line-down risk" in t.lower():
        out["line_down"] = True

    return out

# -----------------------------------------------------------------------------
# Cleaning (post-extraction validation)
# -----------------------------------------------------------------------------
def _clean_incident(x: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "part_id" in x and isinstance(x["part_id"], str) and x["part_id"].strip():
        out["part_id"] = x["part_id"].strip()

    if "qty" in x:
        v = _coerce_int(x["qty"])
        if v is not None and v > 0:
            out["qty"] = v

    if "required_by_hours" in x:
        v = _coerce_float(x["required_by_hours"])
        if v is not None and v > 0:
            out["required_by_hours"] = v

    if "line_down" in x:
        v = _coerce_bool(x["line_down"])
        if v is not None:
            out["line_down"] = v

    if "dc_on_hand" in x:
        v = _coerce_int(x["dc_on_hand"])
        if v is not None and v >= 0:
            out["dc_on_hand"] = v

    if "supplier_lead_time_hours" in x:
        v = _coerce_float(x["supplier_lead_time_hours"])
        if v is not None and v > 0:
            out["supplier_lead_time_hours"] = v

    return out

def _clean_policy(x: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    if "expedite_budget_usd" in x:
        v = _coerce_float(x["expedite_budget_usd"])
        if v is not None and v >= 0:
            out["expedite_budget_usd"] = v

    if "downtime_cost_per_hour_usd" in x:
        v = _coerce_float(x["downtime_cost_per_hour_usd"])
        if v is not None and v >= 0:
            out["downtime_cost_per_hour_usd"] = v

    if "carrier_capacity_score" in x:
        v = _coerce_float(x["carrier_capacity_score"])
        if v is not None and 0.0 <= v <= 1.0:
            out["carrier_capacity_score"] = v

    if "disruption_risk" in x and isinstance(x["disruption_risk"], str):
        v = x["disruption_risk"].strip().lower()
        if v in {"low", "medium", "high"}:
            out["disruption_risk"] = v

    return out

def _clean_ops(x: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in ("ready_to_tender", "pick_pack_complete", "compliance_cleared"):
        if k in x:
            v = _coerce_bool(x[k])
            if v is not None:
                out[k] = v
    return out

def _clean_feas(x: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "premium_mode_available" in x:
        v = _coerce_bool(x["premium_mode_available"])
        if v is not None:
            out["premium_mode_available"] = v
    if "economy_mode_available" in x:
        v = _coerce_bool(x["economy_mode_available"])
        if v is not None:
            out["economy_mode_available"] = v
    if "next_milk_run_departure_hours" in x:
        v = _coerce_float(x["next_milk_run_departure_hours"])
        if v is not None and v >= 0:
            out["next_milk_run_departure_hours"] = v
    return out

# -----------------------------------------------------------------------------
# Stage-aware extraction with hard gating (prevents "Hello" from moving anything)
# -----------------------------------------------------------------------------
async def _extract_incident(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return _clean_incident(_subset_dict(payload, sorted(INCIDENT_KEYS)))

    if isinstance(payload, str):
        # Fast deterministic parse first.
        parsed = _clean_incident(_parse_incident_text(payload))
        if parsed:
            return parsed

        # If the text doesn't look like an incident, do not ask the LLM.
        low = payload.lower()
        if not any(w in low for w in ["part", "qty", "quantity", "dc", "on hand", "lead time", "within", "hours", "line down", "line-down"]):
            return {}

        # Fallback to LLM (still cleaned).
        raw = await llm_client.extract(
            incoming=payload,
            allowed_fields=sorted(INCIDENT_KEYS),
            intro=f"You are {AGENT_ID}. Extract incident fields. Return {{}} if irrelevant. Never guess.",
            context={
                "stage": "A",
                "mode": "incident",
                "expected_keys": sorted(INCIDENT_KEYS),
            },
        )
        return _clean_incident(raw)

    return {}

async def _extract_policy(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return _clean_policy(_subset_dict(payload, sorted(POLICY_KEYS)))

    if isinstance(payload, str):
        low = payload.lower()
        if not any(w in low for w in ["budget", "downtime", "cost", "expedite", "risk", "capacity", "$"]):
            return {}
        raw = await llm_client.extract(
            incoming=payload,
            allowed_fields=sorted(POLICY_KEYS),
            intro=(
                f"You are {AGENT_ID}. Extract amendment/policy fields.\n"
                "Only extract fields that are explicitly stated.\n"
                "Return {} if irrelevant.\n"
                "Return numbers as JSON numbers (no '$', no commas, no '/hour', no words)."
            ),
            context={
                "stage": "B",
                "mode": "amendment",
                "expected_keys": sorted(POLICY_KEYS),
            },
        )
        return _clean_policy(raw)

    return {}

async def _extract_ops(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return _clean_ops(_subset_dict(payload, sorted(OPS_KEYS)))

    if isinstance(payload, str):
        low = payload.lower()
        if not any(w in low for w in ["ready", "tender", "pick", "pack", "compliance", "cleared"]):
            return {}
        raw = await llm_client.extract(
            incoming=payload,
            allowed_fields=sorted(OPS_KEYS),
            intro=f"You are {AGENT_ID}. Extract ops readiness fields. Return {{}} if irrelevant. Never guess.",
            context={
                "stage": "B",
                "mode": "ops_progress",
                "expected_keys": sorted(OPS_KEYS),
            },
        )
        return _clean_ops(raw)

    return {}

async def _extract_feas(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        return _clean_feas(_subset_dict(payload, sorted(FEAS_KEYS)))

    if isinstance(payload, str):
        low = payload.lower()
        if not any(w in low for w in ["premium", "economy", "available", "milk run", "departure"]):
            return {}
        raw = await llm_client.extract(
            incoming=payload,
            allowed_fields=sorted(FEAS_KEYS),
            intro=f"You are {AGENT_ID}. Extract feasibility fields. Return {{}} if irrelevant. Never guess.",
            context={
                "stage": "C",
                "mode": "feasibility",
                "expected_keys": sorted(FEAS_KEYS),
            },
        )
        return _clean_feas(raw)

    return {}

# -----------------------------------------------------------------------------
# Decisions
# -----------------------------------------------------------------------------
def _decide_source_f_vs_g() -> Optional[str]:
    """
    Decide A->B:
      - require qty
      - choose f if dc_on_hand >= qty
      - else choose g if supplier_lead_time_hours exists
      - else None
    """
    qty = _coerce_int(case.get("qty"))
    if qty is None:
        return None

    dc = _coerce_int(case.get("dc_on_hand"))
    if dc is not None and dc >= qty:
        return "f"

    lt = _coerce_float(case.get("supplier_lead_time_hours"))
    if lt is not None:
        return "g"

    return None

def _decide_intent_p_vs_q() -> Optional[str]:
    """
    Decide 2-cell intent (does NOT change object stage):
      - require downtime_cost_per_hour_usd, expedite_budget_usd, required_by_hours, and line_down
      - choose p if downtime * required_by_hours >= expedite_budget AND reliability looks OK
      - else q
    Optional: low capacity + high disruption can force q even if economics suggest p.
    """
    line_down = _coerce_bool(case.get("line_down"))
    req_h = _coerce_float(case.get("required_by_hours"))

    budget = _coerce_float(policy.get("expedite_budget_usd"))
    downtime = _coerce_float(policy.get("downtime_cost_per_hour_usd"))

    if line_down is None or req_h is None:
        return None
    if budget is None or downtime is None:
        return None

    econ_justified = (line_down is True) and (downtime * req_h >= budget)

    cap = _coerce_float(policy.get("carrier_capacity_score"))
    risk = policy.get("disruption_risk")

    reliability_ok = (cap is None) or (cap >= 0.4)
    reliability_bad = (cap is not None) and (cap < 0.4) and (risk == "high")

    if econ_justified and reliability_ok and not reliability_bad:
        return "p"
    return "q"

def _ops_ready() -> bool:
    rtt = _coerce_bool(ops.get("ready_to_tender"))
    if rtt is True:
        return True
    pp = _coerce_bool(ops.get("pick_pack_complete"))
    cc = _coerce_bool(ops.get("compliance_cleared"))
    return (pp is True) and (cc is True)

def _decide_ship_lane() -> Optional[str]:
    """
    Decide C->D conditioned on intent_lane and feasibility:
      - require intent_lane
      - require explicit premium/economy availability
      - follow intent if available else override if possible
    """
    intent = history.get("intent_lane")
    if intent not in {"p", "q"}:
        return None

    prem = _coerce_bool(feas.get("premium_mode_available"))
    econ = _coerce_bool(feas.get("economy_mode_available"))
    if prem is None and econ is None:
        return None

    if intent == "p":
        if prem is True:
            return "p"
        if prem is False and econ is True:
            history["exception"] = "intent=p but premium unavailable; executed q"
            return "q"
        return None

    # intent == q
    if econ is True:
        return "q"
    if econ is False and prem is True:
        history["exception"] = "intent=q but economy unavailable; executed p"
        return "p"
    return None

def _display_tokens() -> List[Node]:
    """
    Tokens we WANT green in the viz (history-aware).
    Keep these separate from tape decisions.
    """
    toks: List[str] = []
    for k in ("source_choice", "amendment_choice", "intent_lane", "shipment_lane"):
        v = history.get(k)
        if isinstance(v, str) and v.strip():
            toks.append(v.strip())
    # h is meaningful once we reached C or D
    if history.get("stage") in {"C", "D"}:
        toks.append("h")
    return [Node(t) for t in toks]

# -----------------------------------------------------------------------------
# Report + evaluation
# -----------------------------------------------------------------------------
def _closure_report() -> Dict[str, Any]:
    return {
        "scenario": "critical_spare_part_fulfillment",
        "path": {
            "stage": history.get("stage"),
            "source_choice": history.get("source_choice"),
            "amendment_choice": history.get("amendment_choice"),
            "intent_lane": history.get("intent_lane"),
            "shipment_lane": history.get("shipment_lane"),
            "exception": history.get("exception"),
        },
        "case": {k: case.get(k) for k in sorted(INCIDENT_KEYS) if k in case},
        "policy": {k: policy.get(k) for k in sorted(POLICY_KEYS) if k in policy},
        "ops": {k: ops.get(k) for k in sorted(OPS_KEYS) if k in ops},
        "feasibility": {k: feas.get(k) for k in sorted(FEAS_KEYS) if k in feas},
        "evaluation": history.get("evaluation"),
    }

def _evaluate_at_D(final_input: Any) -> Dict[str, Any]:
    """
    Accept ground truth with either:
      - new keys: source_choice, amendment_choice, intent_lane, shipment_lane
      - old keys: choice_1cell, choice_2cell, intent, ship_choice
    """
    out: Dict[str, Any] = {
        "path": {
            "source_choice": history.get("source_choice"),
            "amendment_choice": history.get("amendment_choice"),
            "intent_lane": history.get("intent_lane"),
            "shipment_lane": history.get("shipment_lane"),
        },
        "exception": history.get("exception"),
        "has_exception": history.get("exception") is not None,
    }

    if isinstance(final_input, dict) and isinstance(final_input.get("ground_truth"), dict):
        gt = final_input["ground_truth"]

        # Backward compatible mapping.
        mapped = {
            "source_choice": gt.get("source_choice", gt.get("choice_1cell")),
            "amendment_choice": gt.get("amendment_choice", gt.get("choice_2cell")),
            "intent_lane": gt.get("intent_lane", gt.get("intent")),
            "shipment_lane": gt.get("shipment_lane", gt.get("ship_choice")),
        }

        checks = {}
        for k in ("source_choice", "amendment_choice", "intent_lane", "shipment_lane"):
            if mapped.get(k) is not None:
                checks[k] = {
                    "expected": mapped.get(k),
                    "actual": history.get(k),
                    "match": mapped.get(k) == history.get(k),
                }
        out["ground_truth_checks"] = checks
        if checks:
            out["all_matches"] = all(v["match"] for v in checks.values())

    return out

# -----------------------------------------------------------------------------
# Tape state
# -----------------------------------------------------------------------------
states: List[Node] = [Node("A")]
state_lock = asyncio.Lock()

# -----------------------------------------------------------------------------
# Summoner client + flow
# -----------------------------------------------------------------------------
client = SummonerClient(name=AGENT_ID)
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
Trigger = client_flow.triggers()

# -----------------------------------------------------------------------------
# State upload/download (priority + anchoring by memory.stage)
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# State upload/download (priority + anchoring by memory.stage)
# -----------------------------------------------------------------------------
@client.upload_states()
async def upload_states(_: Any) -> list[str]:
    global states

    # ALIGNMENT: always acquire mem_lock before state_lock when both are needed.
    async with mem_lock, state_lock:
        # Ensure object for the current stage is always present in states.
        stage = history.get("stage") or "A"
        obj = Node(stage)
        if obj not in states:
            states = [obj]

        # Build display snapshot while locks are held.
        display = list({*states, *_display_tokens()})

        # Return tape states (not display states) to Summoner.
        tape = [obj]
        src = history.get("source_choice")
        if stage in {"B", "C", "D"} and src in {"f", "g"}:
            tape.append(Node(src))

    # Push to viz outside locks.
    viz.push_states(display)
    return tape


@client.download_states()
async def state_processor(possible_states: list[Node]) -> None:
    """
    Priority: D > C > B > A

    Anchoring rule:
    - If tape output is missing objects, trust history.stage to keep the workflow stable.
    - B must persist until C exists.
    """
    global states
    ps = list(set(possible_states))

    async with mem_lock:
        stage = history.get("stage") or "A"
        src = history.get("source_choice")

    def anchor(stage_letter: str) -> List[Node]:
        out = [Node(stage_letter)]
        if stage_letter in {"B", "C", "D"} and src in {"f", "g"}:
            out.append(Node(src))
        return out

    # If D appears anywhere, we're at D.
    if Node("D") in ps or stage == "D":
        async with state_lock:
            states = anchor("D")

        viz.push_states(list({*states, *_display_tokens()}))

        # Reset after report was emitted.
        do_reset = False
        async with mem_lock:
            if report_emitted:
                _reset_all()
                do_reset = True

        if do_reset:
            async with state_lock:
                states = [Node("A")]
            viz.push_states(list({*states, *_display_tokens()}))

        return

    # If C appears anywhere, we're at C.
    if Node("C") in ps or stage == "C":
        async with state_lock:
            states = anchor("C")
        viz.push_states(list({*states, *_display_tokens()}))
        return

    # If B appears anywhere OR stage says B, we're at B.
    if Node("B") in ps or stage == "B":
        async with state_lock:
            states = anchor("B")
        viz.push_states(list({*states, *_display_tokens()}))
        return

    # Otherwise A.
    async with state_lock:
        states = [Node("A")]
    viz.push_states(list({*states, *_display_tokens()}))

# -----------------------------------------------------------------------------
# Hooks
# -----------------------------------------------------------------------------
@client.hook(direction=Direction.RECEIVE)
async def validate_incoming(msg: Any) -> Optional[dict]:
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        return None
    content = msg.get("content")
    if isinstance(content, str) and not content.strip():
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
# Receives: 1-cells (A->B)
# -----------------------------------------------------------------------------
@client.receive(route=ROUTE_F)
async def arrow_f_A_B(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "A":
            return Stay(Trigger.ok)
        if history.get("source_choice") is not None:
            return Stay(Trigger.ok)

    extracted = await _extract_incident(msg["content"])
    await aprint(f"\033[34m[f] incident extracted: {json.dumps(extracted, indent=2)}\033[0m")
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        _merge(case, extracted)
        decision = _decide_source_f_vs_g()
        if decision == "f":
            history["source_choice"] = "f"
            history["stage"] = "B"
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

@client.receive(route=ROUTE_G)
async def arrow_g_A_B(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "A":
            return Stay(Trigger.ok)
        if history.get("source_choice") is not None:
            return Stay(Trigger.ok)

    extracted = await _extract_incident(msg["content"])
    await aprint(f"\033[34m[g] incident extracted: {json.dumps(extracted, indent=2)}\033[0m")
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        _merge(case, extracted)
        decision = _decide_source_f_vs_g()
        if decision == "g":
            history["source_choice"] = "g"
            history["stage"] = "B"
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

# -----------------------------------------------------------------------------
# Receives: 2-cells (between transitions)
#   - Do NOT advance object stage.
#   - Allow co-trigger with h if payload contains both policy and ops keys.
# -----------------------------------------------------------------------------
def _decide_intent_p_vs_q_best_effort() -> tuple[str, str]:
    """
    Best-effort intent decision.
    Always returns ("p" or "q", reason_string).

    Priority:
      1) Use the normal rule (_decide_intent_p_vs_q) if it returns p/q.
      2) Fallback from incident context (line_down + required_by_hours).
      3) Conservative default: q
    """
    intent = _decide_intent_p_vs_q()
    if intent in {"p", "q"}:
        return intent, "rule: policy-based intent"

    # --- fallback: use incident context (works even if policy extraction failed) ---
    line_down = _coerce_bool(case.get("line_down"))
    required_by = case.get("required_by_hours")
    try:
        required_by_h = float(required_by) if required_by is not None else None
    except Exception:
        required_by_h = None

    # If the line is down and deadline is tight, assume expedite intent.
    if line_down is True and required_by_h is not None and required_by_h <= 12.0:
        return "p", "fallback: line_down + required_by_hours<=12"

    return "q", "fallback: conservative default (insufficient policy signal)"


async def _handle_2cell(msg: Any, *, option_label: str, required_source: str, intent_if_chosen: str) -> Event:
    async with mem_lock:
        if history.get("stage") != "B":
            return Stay(Trigger.ok)
        if history.get("source_choice") != required_source:
            return Stay(Trigger.ok)
        if history.get("amendment_choice") is not None:
            return Stay(Trigger.ok)

    extracted = await _extract_policy(msg["content"])
    await aprint(f"\033[34m[{option_label}] policy extracted: {json.dumps(extracted, indent=2)}\033[0m")

    # Hard guard: do not pick an amendment if no policy fields were extracted.
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        # Merge if we got something, but do NOT require it.
        if extracted:
            _merge(policy, extracted)

        # Decide intent with best-effort context, never None.
        intent, reason = _decide_intent_p_vs_q_best_effort()

        # Only the matching 2-cell should fire.
        desired = "p" if option_label.startswith("eta_") else "q"
        if intent != desired:
            return Stay(Trigger.ok)

        history["amendment_choice"] = option_label
        history["intent_lane"] = intent_if_chosen

        # Optional: persist why we chose this (helps analytics / debugging)
        history["amendment_reason"] = reason

        return Move(Trigger.ok)

@client.receive(route=ROUTE_ETA_F)
async def arrow_eta_f(msg: Any) -> Event:
    return await _handle_2cell(msg, option_label="eta_f", required_source="f", intent_if_chosen="p")

@client.receive(route=ROUTE_MU_F)
async def arrow_mu_f(msg: Any) -> Event:
    return await _handle_2cell(msg, option_label="mu_f", required_source="f", intent_if_chosen="q")

@client.receive(route=ROUTE_ETA_G)
async def arrow_eta_g(msg: Any) -> Event:
    return await _handle_2cell(msg, option_label="eta_g", required_source="g", intent_if_chosen="p")

@client.receive(route=ROUTE_MU_G)
async def arrow_mu_g(msg: Any) -> Event:
    return await _handle_2cell(msg, option_label="mu_g", required_source="g", intent_if_chosen="q")

# -----------------------------------------------------------------------------
# Receives: B->C progression (h)
#   - Requires ops readiness fields.
#   - Can co-trigger with 2-cell if payload merges both parts.
# -----------------------------------------------------------------------------
@client.receive(route=ROUTE_H)
async def arrow_h_B_C(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "B":
            return Stay(Trigger.ok)
        if history.get("source_choice") not in {"f", "g"}:
            return Stay(Trigger.ok)

    extracted = await _extract_ops(msg["content"])
    await aprint(f"\033[34m[h] ops extracted: {json.dumps(extracted, indent=2)}\033[0m")
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        _merge(ops, extracted)
        if _ops_ready():
            history["stage"] = "C"
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

# -----------------------------------------------------------------------------
# Receives: C->D shipping (p/q)
#   - Requires intent_lane and feasibility booleans.
# -----------------------------------------------------------------------------
@client.receive(route=ROUTE_P)
async def arrow_p_C_D(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "C":
            return Stay(Trigger.ok)
        if history.get("shipment_lane") is not None:
            return Stay(Trigger.ok)
        if history.get("intent_lane") not in {"p", "q"}:
            return Stay(Trigger.ok)

    extracted = await _extract_feas(msg["content"])
    await aprint(f"\033[34m[p] feasibility extracted: {json.dumps(extracted, indent=2)}\033[0m")
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        _merge(feas, extracted)
        decision = _decide_ship_lane()
        if decision == "p":
            history["shipment_lane"] = "p"
            history["stage"] = "D"
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

@client.receive(route=ROUTE_Q)
async def arrow_q_C_D(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "C":
            return Stay(Trigger.ok)
        if history.get("shipment_lane") is not None:
            return Stay(Trigger.ok)
        if history.get("intent_lane") not in {"p", "q"}:
            return Stay(Trigger.ok)

    extracted = await _extract_feas(msg["content"])
    await aprint(f"\033[34m[q] feasibility extracted: {json.dumps(extracted, indent=2)}\033[0m")
    if not extracted:
        return Stay(Trigger.ok)

    async with mem_lock:
        _merge(feas, extracted)
        decision = _decide_ship_lane()
        if decision == "q":
            history["shipment_lane"] = "q"
            history["stage"] = "D"
            return Move(Trigger.ok)
        return Stay(Trigger.ok)

# -----------------------------------------------------------------------------
# Objects and cells
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

@client.receive(route="D")
async def object_D(msg: Any) -> Event:
    async with mem_lock:
        if history.get("stage") != "D":
            return Test(Trigger.ok)
        content = msg["content"] if (isinstance(msg, dict) and "content" in msg) else msg
        history["evaluation"] = _evaluate_at_D(content)
    return Test(Trigger.ok)

# Keep token handlers minimal and non-noisy.
# They exist so the tape doesn't complain if a token is active,
# but they do NOT emit extra traces.
@client.receive(route="f")
async def cell_f(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="g")
async def cell_g(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="h")
async def cell_h(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="p")
async def cell_p(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="q")
async def cell_q(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="eta_f")
async def cell_eta_f(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="mu_f")
async def cell_mu_f(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="eta_g")
async def cell_eta_g(_: Any) -> Event:
    return Stay(Trigger.ok)

@client.receive(route="mu_g")
async def cell_mu_g(_: Any) -> Event:
    return Stay(Trigger.ok)

# -----------------------------------------------------------------------------
# Send handlers (trace messages)
# -----------------------------------------------------------------------------
@client.send(route="A--[f]-->B", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_f() -> str:
    return "Moved A -> B via f (regional DC selected)"

@client.send(route="A--[f]-->B", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_f() -> str:
    return "Stayed on A via f (incident insufficient for DC selection, or supplier path better)"

@client.send(route="A--[g]-->B", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_g() -> str:
    return "Moved A -> B via g (supplier selected)"

@client.send(route="A--[g]-->B", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_g() -> str:
    return "Stayed on A via g (incident insufficient for supplier selection, or DC path better)"

@client.send(route="f--[eta_f]-->p", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_eta_f() -> str:
    return "Selected eta_f: expedite authorization (intent -> p)"

@client.send(route="f--[mu_f]-->q", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_mu_f() -> str:
    return "Selected mu_f: consolidation mandate (intent -> q)"

@client.send(route="g--[eta_g]-->p", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_eta_g() -> str:
    return "Selected eta_g: supplier expedite clause (intent -> p)"

@client.send(route="g--[mu_g]-->q", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_mu_g() -> str:
    return "Selected mu_g: standard clause (intent -> q)"

@client.send(route="B--[h]-->C", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_h() -> str:
    return "Moved B -> C via h (ops ready: pick/pack + compliance complete)"

@client.send(route="B--[h]-->C", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_h() -> str:
    return "Stayed on B via h (waiting for ops readiness fields)"

@client.send(route="C--[p]-->D", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_p() -> str:
    return "Moved C -> D via p (premium shipment executed)"

@client.send(route="C--[p]-->D", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_p() -> str:
    return "Stayed on C via p (not chosen, or feasibility missing)"

@client.send(route="C--[q]-->D", on_actions={Action.MOVE}, on_triggers={Trigger.ok})
async def send_move_q() -> str:
    return "Moved C -> D via q (economy shipment executed)"

@client.send(route="C--[q]-->D", on_actions={Action.STAY}, on_triggers={Trigger.ok})
async def send_stay_q() -> str:
    return "Stayed on C via q (not chosen, or feasibility missing)"

@client.send(route="A", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_A() -> str:
    return "A processed and forgotten"

@client.send(route="B", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_B() -> str:
    return "B processed and forgotten"

@client.send(route="C", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_C() -> str:
    return "C processed and forgotten"

@client.send(route="D", on_actions={Action.TEST}, on_triggers={Trigger.ok})
async def send_D() -> Any:
    global report_emitted
    async with mem_lock:
        # If a stray D token fires after we reset stage to A, suppress output.
        if history.get("stage") != "D":
            return None  # dropped by the SEND hook (since it's not str/dict)
        if report_emitted:
            return "D processed and forgotten"
        rep = _closure_report()
        report_emitted = True
        return {"message": rep}

# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the supply-chain 2-category agent.")
    parser.add_argument("--config", dest="config_path", required=False, help="Client config path")
    args = parser.parse_args()

    viz.start(open_browser=True)
    client_flow.compile_arrow_patterns()
    viz.set_graph_from_dna(json.loads(client.dna()), parse_route=client_flow.parse_route)
    viz.push_states(states)

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
