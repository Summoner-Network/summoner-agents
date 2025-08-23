from pathlib import Path
from typing import Any, Optional, Union
import json, asyncio


class Style:
    """ANSI styling helper with an on/off toggle."""
    _ENABLED: bool = True
    RESET = "\x1b[0m"
    BOLD = "1"
    COLOR_CODES = {
        # basics
        "black":   "30",
        "red":     "31",
        "green":   "32",
        "yellow":  "33",
        "blue":    "34",
        "magenta": "35",
        "cyan":    "36",
        "white":   "37",
        # bright variants
        "bright_black":   "90",
        "bright_red":     "91",
        "bright_green":   "92",
        "bright_yellow":  "93",
        "bright_blue":    "94",
        "bright_magenta": "95",
        "bright_cyan":    "96",
        "bright_white":   "97",
        # aliases used in docs
        "gray":    "90",
    }

    @classmethod
    def enable_colors(cls, enabled: bool) -> None:
        cls._ENABLED = bool(enabled)

    @classmethod
    def format(cls, s: str, color: Optional[str] = None, bold: bool = False) -> str:
        if not cls._ENABLED:
            return s
        codes: list[str] = []
        if bold:
            codes.append(cls.BOLD)
        if color:
            code = cls.COLOR_CODES.get(color)
            # fall back silently if unknown color name is used
            if code:
                codes.append(code)
        if not codes:
            return s
        return f"\x1b[{';'.join(codes)}m{s}{cls.RESET}"


class Text:
    @staticmethod
    def normalize(s: Any) -> str:
        return str(s).strip().lower()


class Questions:
    """
    Questionnaire container. User controls the source.

    Initialize with either:
      - source="path/to/questions.json"  (relative paths resolved against `base` or CWD)
      - data=[{...}, {...}]              (already-loaded list)
    """

    def __init__(
        self,
        source: Optional[Union[str, Path]] = None,
        data: Optional[list[dict[str, Any]]] = None,
        limit: Optional[int] = None,
        base: Optional[Union[str, Path]] = None,
    ) -> None:
        if (source is None) and (data is None):
            raise ValueError(
                "No questionnaire provided. Initialize Questions with `source=...` or `data=[...]`."
            )

        if data is not None:
            questions = data
        else:
            p = Path(source)
            if not p.is_absolute():
                p = Path(base or ".").resolve() / p
            try:
                with p.open("r", encoding="utf-8") as f:
                    questions = json.load(f)
            except FileNotFoundError as e:
                raise FileNotFoundError(f"Question file not found: {p}") from e
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in question file: {p}") from e

        if not isinstance(questions, list):
            raise TypeError("Questionnaire JSON must be a list of question objects.")

        for i, q in enumerate(questions):
            if not isinstance(q, dict) or "question" not in q or "answers" not in q:
                raise ValueError(f"Malformed question at index {i}: expected keys 'question' and 'answers'.")

        if limit is not None:
            questions = questions[:limit]

        self._questions: list[dict[str, Any]] = questions

    # Pythonic conveniences
    def __len__(self) -> int:
        return len(self._questions)

    def __getitem__(self, idx: int) -> dict[str, Any]:
        return self._questions[idx]

    # Render / scoring
    def render_question(self, idx: int) -> str:
        q = self._questions[idx]
        title = Style.format(f"Q#{idx}: ", color="blue", bold=True) + Style.format(q["question"], color="blue", bold=False)
        lines = [title]
        for label, obj in q["answers"].items():
            lines.append(f"{Style.format(label, color='cyan', bold=True)}) {obj['val']}")
        lines.append(Style.format("(Answer with the label. 5s window after first answer.)", color="gray"))
        return "\n".join(lines)

    def score_answer(self, content: str, idx: int) -> Optional[int]:
        """Return points if content matches the label (A/B/C)."""
        norm = Text.normalize(content)
        for label, obj in self._questions[idx]["answers"].items():
            if norm == Text.normalize(label):
                return int(obj["pts"])
        return None


class ScoreKeeper:
    """
    Minimal score management with rendering.
    - add(addr, pts, idx_ans) updates scores and returns a 'winner' message string.
    - render(top_n=5) returns the scoreboard string.
    """

    def __init__(self) -> None:
        self._scores: dict[str, int] = {}

    def add(self, addr: str, pts: int, idx_ans: int) -> str:
        """Add points for `addr` and return a pre-formatted winner message."""
        self._scores[addr] = self._scores.get(addr, 0) + pts

        winner = Style.format("Winner:", color="magenta", bold=True)
        addr_c = Style.format(addr, color="cyan")
        pts_c  = Style.format(str(pts), color="green", bold=True)
        return f"{winner} {addr_c} best answered Q#{idx_ans}, earning {pts_c} points."

    def clear(self) -> None:
        self._scores.clear()

    def view(self) -> dict[str, int]:
        """Expose raw mapping (useful if callers need direct access)."""
        return self._scores

    def render(self, top_n: int = 5) -> str:
        """Return a formatted scoreboard."""
        if not self._scores:
            return Style.format("Scoreboard: (no scores yet)", color="magenta", bold=True)
        top = sorted(self._scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        lines = [Style.format("Scoreboard:", color="magenta", bold=True)]
        for i, (addr, pts) in enumerate(top, 1):
            lines.append(
                f"{Style.format(str(i)+'.', color='cyan', bold=True)} "
                f"{Style.format(addr, color='cyan')} — {Style.format(str(pts)+' pts', color='green')}"
            )
        return "\n".join(lines)


class Countdown:
    """Lightweight local countdown for human feedback."""
    ENABLED: bool = True

    @classmethod
    def configure(cls, enabled: bool) -> None:
        cls.ENABLED = bool(enabled)

    @classmethod
    async def start(cls, seconds: int, stop: asyncio.Event) -> None:
        if not cls.ENABLED:
            return
        try:
            for remaining in range(seconds, 0, -1):
                if stop.is_set():
                    break
                print(
                    f"\r{Style.format('[grading]', color='blue', bold=True)} "
                    f"{Style.format(str(remaining)+'s left...', color='gray')}",
                    end="",
                    flush=True,
                )
                await asyncio.sleep(1)
        finally:
            # Clear line
            print("\r" + " " * 48 + "\r", end="", flush=True)
