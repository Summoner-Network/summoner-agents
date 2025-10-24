from summoner.client import SummonerClient
from typing import Any, Optional, Literal
import argparse, asyncio, time
from aioconsole import aprint

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
# Internal state for tracking the exam process.
question_tracker: Optional[int] = None           # index of current question (None until start)
score = ScoreKeeper()                            # cumulative points per participant + rendering
answer_buffer: Optional[asyncio.Queue] = None    # holds (addr, idx, pts, ts) for each answer received
variables_lock: Optional[asyncio.Lock] = None    # ensures updates happen safely across tasks  # CHANGED: created in setup()
phase: Literal["none", "start", "ongoing"] = "none"  # tracks progression through exam phases

client = SummonerClient(name="ExamAgent_0")

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

    # Hard-filter to current question if available to avoid late answers from other questions.
    current_only = [t for t in candidates if t[1] == current_idx]  # NEW
    if not current_only:                                           # NEW
        # No valid answers for the current question
        return ("", current_idx, 0, float("inf"))                  # NEW

    # Apply the same tie-break rules as before.
    candidates_sorted = sorted(
        current_only,                                              # CHANGED: use current_only
        key=lambda t: (-t[2], t[3])                                # points desc, earlier ts
    )
    return candidates_sorted[0]


async def _drain(q: asyncio.Queue) -> None:  # NEW: drain helper to clear stale answers
    try:
        while True:
            q.get_nowait()
            q.task_done()
    except asyncio.QueueEmpty:
        pass


# ---------------- Setup ----------------
async def setup() -> None:
    """Prepare shared state before the client starts."""
    global answer_buffer, variables_lock
    variables_lock = asyncio.Lock()           # CHANGED: bind to client loop
    answer_buffer = asyncio.Queue()           # CHANGED: bind to client loop

# ---------------- RECEIVE ----------------
@client.receive(route="")
async def receive_response(msg: Any) -> None:
    """
    Handle responses from participants.
    Valid answers are pushed into the buffer for later grading.
    """
    global question_tracker, phase, answer_buffer, variables_lock
    assert answer_buffer is not None and variables_lock is not None  # NEW: sanity

    content = (msg.get("content") if isinstance(msg, dict) else msg)
    addr    = (msg.get("remote_addr") if isinstance(msg, dict) else "unknown")

    if not isinstance(content, str): 
        return

    # Print all incoming messages for visibility
    if str(content).startswith("Warning:"):
        await aprint("\r[From server]", str(content)) # server-originated warning; no addr
        return
    else:
        await aprint("\r[Received]", f"{addr} answered: {content}") 
    
    # If this is the first answer received, trigger the start of the exam
    async with variables_lock:
        if question_tracker is None:
            phase = "start"
            return
        idx_snapshot = question_tracker

    # Evaluate the answer and assign points if it matches
    pts = qset.score_answer(content, idx_snapshot)
    if pts is None: 
        return 

    await answer_buffer.put((addr, idx_snapshot, pts, time.monotonic()))

# ---------------- SEND ----------------
@client.send(route="")
async def send_driver() -> str:
    """
    Drive the exam by sending questions, collecting answers,
    grading, updating the scoreboard, and advancing rounds.
    """
    global question_tracker, phase, answer_buffer, variables_lock
    assert answer_buffer is not None and variables_lock is not None  # NEW: sanity

    # Exam not started: no outbound message
    # Exam just started: send first question
    async with variables_lock:
        if phase == "none":
            return
        if phase == "start":
            phase = "ongoing"
            question_tracker = 0
            await _drain(answer_buffer)  # NEW: clear any stale answers before first question
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
    if addr and pts > 0 and idx_ans == idx_snapshot:  # CHANGED: guard against no-current-answer case
        result_message = score.add(addr, pts, idx_ans)  # returns the formatted "Winner: ..." line
    else:
        result_message = f"No participant managed to answer Q#{idx_snapshot}"  # CHANGED

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
    if leave_exam and phase == "ongoing":
        next_step_message = Style.format("Scoreboard reset — new round begins!", color="magenta", bold=True)
        score.clear()
        async with variables_lock:
            phase = "none"
        await _drain(answer_buffer)  # NEW: clear late arrivals at round end
    else:
        next_step_message = qset.render_question(idx_snapshot)

    # Final outbound message: show results, current scoreboard, and next step
    return "\n\n".join([result_message,scoreboard,next_step_message]) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='Path to the client config JSON (e.g., --config configs/client_config.json)')
    args, _ = parser.parse_known_args()

    client.loop.run_until_complete(setup())

    client.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
