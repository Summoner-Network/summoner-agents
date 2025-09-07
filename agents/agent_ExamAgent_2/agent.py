import os
import sys
from urllib.parse import urlparse
from summoner.client import SummonerClient
from summoner.protocol import Direction, Move, Stay, Node, Event
from typing import Any, Optional, Literal
import argparse, asyncio, time

# --- Helpers (shared utilities) ---
from exam_utils import (
    Style,
    Questions,
    Countdown,
    ScoreKeeper,
)

# ---------------- CLI (options for visual aids) ----------------
# These flags control optional features like color output and local countdown timers.
opts_parser = argparse.ArgumentParser(add_help=False)
opts_parser.add_argument("--colors",    type=int, choices=[0, 1], default=1, help="Enable ANSI color output (1=on, 0=off). Default: 1.")
opts_parser.add_argument("--countdown", type=int, choices=[0, 1], default=1, help="Show a local countdown during grading (1=on, 0=off). Default: 1.")
opts_parser.add_argument('--qa', dest='qa_path', required=True, help='Path to a questions JSON file (see format in exam_utils).')

opts, _ = opts_parser.parse_known_args()
COLORS_ENABLED    = bool(int(opts.colors))
COUNTDOWN_ENABLED = bool(int(opts.countdown))

# Configure helpers according to CLI flags
Style.enable_colors(COLORS_ENABLED)
Countdown.configure(COUNTDOWN_ENABLED)

# ---------------- Data load ----------------
# Load a set of questions, with answers and points. 
# Here we limit to 2 questions for demonstration.
qset = Questions(source=opts.qa_path, limit=2)

# ---------------- State ----------------
# Internal state for the exam flow (two states with the flow engine).
question_tracker: Optional[int] = None           # index of current question (None until started)
score = ScoreKeeper()                            # cumulative points per participant + rendering
answer_buffer: Optional[asyncio.Queue] = None    # holds (addr, idx, pts, ts) per answer received
variables_lock = asyncio.Lock()                  # guards shared updates across tasks
phase: Literal["none", "started"] = "none"       # current flow state (reported via @upload_states)

client = SummonerClient(name="ExamAgent_1")

# ---- Activate the automaton/flow engine --------------------------------
# The flow engine orchestrates which @receive(route=...) runs based on state
# and handler return values (Move/Stay).
client_flow = client.flow().activate()
client_flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
client_flow.ready()

# ---- Triggers ---------------------------------------------------------------
# Triggers are loaded dynamically (from TRIGGERS file). For this demo, 'ok' exists.
Trigger = client_flow.triggers()

# ---------------- Local helpers (agent-internal) ----------------
async def collect_answers_window(
    queue: asyncio.Queue,
    window_seconds: float,
    show_countdown: bool,
) -> list[tuple[str, int, int, float]]:
    """
    Collect answers for up to `window_seconds` after the first one arrives.
    Returns a list of (addr, idx, pts, ts) tuples.
    """
    first = await queue.get()
    batch: list[tuple[str, int, int, float]] = [first]

    stop = asyncio.Event()
    printer_task = asyncio.create_task(Countdown.start(int(window_seconds), stop)) if show_countdown else None

    try:
        end = time.monotonic() + window_seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break
    finally:
        stop.set()
        if printer_task:
            try:
                await printer_task
            except Exception:
                pass

    return batch


def pick_winner(
    batch: list[tuple[str, int, int, float]],
    current_idx: int
) -> tuple[str, int, int, float]:
    """
    Choose the winning answer with the following constraints & priority:

    Constraints:
      - Only the FIRST submission per address (IP) is considered per question window; subsequent submissions are ignored.

    Priority among the remaining candidates:
      1) answers to the current question,
      2) higher points,
      3) earlier submission.
    """
    if not batch:
        raise ValueError("pick_winner called with an empty batch")

    # Keep only the first submission (earliest ts) for each address.
    # Note: 'batch' is already built in arrival order, but we still
    # guard on timestamp to be explicit & future-proof.
    first_by_addr: dict[str, tuple[str, int, int, float]] = {}
    for addr, idx, pts, ts in batch:
        if addr not in first_by_addr:
            first_by_addr[addr] = (addr, idx, pts, ts)
        else:
            # Keep the earliest seen (smallest ts)
            _, _, _, ts0 = first_by_addr[addr]
            if ts < ts0:
                first_by_addr[addr] = (addr, idx, pts, ts)

    candidates = list(first_by_addr.values())

    # Apply the same tie-break rules as before.
    candidates_sorted = sorted(
        candidates,
        key=lambda t: (0 if t[1] == current_idx else 1, -t[2], t[3])
    )
    return candidates_sorted[0]


# ---------------- Setup ----------------
async def setup() -> None:
    """Prepare shared state before the client starts."""
    global answer_buffer
    answer_buffer = asyncio.Queue()

# ---- Upload current state to the flow engine --------------------------------
# The flow engine calls this to know our current state; that state is matched
# against @receive(route=...) definitions.
@client.upload_states()
async def state_orchestrator(payload: Any) -> str:
    async with variables_lock:
        return phase

# ---- Integrate possible next states (from handlers) -------------------------
# After handlers return Move/Stay, the engine aggregates "possible" states and
# provides them here. We fold that set back into our local `phase`.
@client.download_states()
async def state_processor(possible_states: list[Node]) -> None:
    global phase
    async with variables_lock:
        if Node("started") in possible_states:
            phase = "started"
        else:
            phase = "none"

# ---------------- HOOKS ----------------
@client.hook(direction=Direction.RECEIVE)
async def validate(msg: Any) -> Optional[dict]:
    
    content = (msg.get("content") if isinstance(msg, dict) else msg)
    addr    = (msg.get("remote_addr") if isinstance(msg, dict) else "unknown")

    if not isinstance(content, str): 
        # Return None to filter this message out
        return

    # Print all incoming messages for visibility
    if str(content).startswith("Warning:"):
        print("\r[From server]", f"{content}", flush=True) # server-originated warning; no addr
        # Return None to filter this message out
        return 
    elif isinstance(msg, str): 
        print("\r[Received]", f"{msg}", flush=True)
        # Return None to filter this message out
        return
    else:
        print("\r[Received]", f"{addr} answered: {content}", flush=True)     

    return msg
    
# ---------------- RECEIVE ----------------
@client.receive(route="none --> started")
async def receive_start_trigger(msg: Any) -> Event:
    """Idle → started: any valid inbound message triggers the round (Q#0 will publish on the next send tick)."""
    return Move(Trigger.ok)

@client.receive(route="started")
async def receive_response(msg: Any) -> Event:
    """While started, accept labels and enqueue scored answers for the active question."""
    global question_tracker, phase

    content = (msg.get("content") if isinstance(msg, dict) else msg)
    addr    = (msg.get("remote_addr") if isinstance(msg, dict) else "unknown")

    # Snapshot active question index; ignore answers if Q#0 hasn't been published yet.
    async with variables_lock:
        idx_snapshot = question_tracker

    if idx_snapshot is None:
        # Question not published yet; ignore gracefully.
        return Stay(Trigger.ok)

    # Evaluate the answer and assign points if it matches
    pts = qset.score_answer(content, idx_snapshot)
    if pts is None: 
        return Stay(Trigger.ok)

    await answer_buffer.put((addr, idx_snapshot, pts, time.monotonic()))

    return Stay(Trigger.ok)

# ---------------- SEND ----------------
@client.send(route="")
async def send_driver() -> str:
    """
    Drive the exam by sending questions, collecting answers,
    grading, updating the scoreboard, and advancing rounds.
    """
    global question_tracker, phase

    # Exam not started: no outbound message
    # Exam just started: send first question
    async with variables_lock:
        if phase == "none":
            return
        if phase == "started" and question_tracker is None:
            question_tracker = 0
            return qset.render_question(0) + "\n"

    # Collect answers during a short window (5s)
    batch = await collect_answers_window(
        answer_buffer,
        window_seconds=5.0,
        show_countdown=COUNTDOWN_ENABLED,
    )

    # Snapshot the current question index for grading
    async with variables_lock:
        idx_snapshot = question_tracker if question_tracker is not None else 0

    # Decide the winner using the tie-break rules
    addr, idx_ans, pts, _ts = pick_winner(batch, idx_snapshot)

    # Build result message and update scoreboard (only if it was for the current question)
    if idx_ans == idx_snapshot:
        result_message = score.add(addr, pts, idx_ans)  # returns the formatted "Winner: ..." line
    else:
        result_message = f"No participant managed to answer Q#{idx_ans}"

    # Render the scoreboard
    scoreboard = score.render()

    # Advance to next question, or wrap around for a new round
    leave_exam = False
    async with variables_lock:
        question_tracker = (question_tracker + 1) % len(qset)
        if question_tracker == 0:
            leave_exam = True
            question_tracker = None
        idx_snapshot = question_tracker

    # If exam round ended, reset scoreboard; otherwise send next question
    next_step_message = ""
    if leave_exam and phase == "started":
        next_step_message = Style.format("Scoreboard reset — new round begins!", color="magenta", bold=True)
        score.clear()
        # We flip back to "none" locally from send(); this is outside the receive/flow graph on purpose.
        phase = "none"
    else:
        next_step_message = qset.render_question(idx_snapshot)

    # Final outbound message: show results, current scoreboard, and next step
    return "\n\n".join([result_message,scoreboard,next_step_message]) + "\n"


if __name__ == "__main__":
    # --- Standard argument parsing ---
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='Path to the client config JSON (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    # --- Get connection details from environment variables ---
    splt_url = os.getenv("SPLT_URL")
    
    # If SPLT_URL is not set, print an error and exit.
    if not splt_url:
        print(f"{Style.format('[ERROR]', color='red', bold=True)} SPLT_URL environment variable is not set.")
        sys.exit(1)

    # Add a default scheme if missing (e.g., "mynlb.com" -> "tcp://mynlb.com")
    if "://" not in splt_url:
        splt_url = f"tcp://{splt_url}"

    # Parse the URL to get the hostname and port
    parsed_url = urlparse(splt_url)
    host = parsed_url.hostname
    port = parsed_url.port or 8888 # Default to port 8888 if not specified

    print(f"Attempting to connect to {Style.format(host, bold=True)} on port {Style.format(port, bold=True)}...")

    # --- Run the client ---
    client.loop.run_until_complete(setup())
    client.run(host=host, port=port, config_path=args.config_path or "configs/client_config.json")