import os
import json
from typing import Any, Callable, Dict, Optional, Tuple

from aioconsole import aprint
from openai import AsyncOpenAI
from dotenv import load_dotenv
load_dotenv()


IntroFn = Callable[..., str]


class LLMClient:
    """
    Async LLM client for Summoner category-structured decisions.

    Output is always a JSON object:
      {"action": "<enum>", "reason": "<string>", "data": {...}}

    You control allowed actions per call. For example:
      - arrow contexts: actions=("move","stay")
      - object contexts: actions=("stay",)
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: str = "You are an assistant helping other agents with their requests.",
        default_intro: str = "You are a minimal agent.",
        intro_fn: Optional[IntroFn] = None,
        debug: bool = False,
    ) -> None:
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Missing api_key (pass api_key=... or set OPENAI_API_KEY).")

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.system_prompt = system_prompt

        self.default_intro = default_intro
        self.intro_fn = intro_fn

        self.debug = debug

    def _format_context(self, context: Dict[str, Any]) -> str:
        """
        Context is printed as JSON so it's unambiguous, including when
        the "route" is reduced to a single object like "A".
        """
        return json.dumps(context, ensure_ascii=False, indent=2)

    def _build_user_prompt(
        self,
        incoming: Dict[str, Any],
        *,
        actions: Tuple[str, ...] = ("move", "stay"),
        intro: Optional[str] = None,
        intro_fn: Optional[IntroFn] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        context = context or {}

        if intro is None:
            fn = intro_fn or self.intro_fn
            intro = fn(**context) if fn is not None else self.default_intro

        allowed = ", ".join(f'"{a}"' for a in actions)

        # Tight, unambiguous, low-noise prompt.
        return (
            f"{intro}\n\n"
            "Context (authoritative JSON):\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "Incoming payload (JSON):\n"
            f"{json.dumps(incoming, ensure_ascii=False, indent=2)}\n\n"
            "Task:\n"
            "- Decide the next action for THIS context only.\n"
            "- Interpret the incoming payload the best you can as an instruction to move through the context's arrow or stay on source.\n"
            "- If the payload is irrelevant to this context, or you cannot act here, choose \"stay\".\n\n"
            "Action meaning:\n"
            "- \"move\": traverse the current arrow now (ONLY if context.mode == \"arrow\").\n"
            "- \"stay\": do not traverse; process in place / no-op. This is the default.\n\n"
            "Output (STRICT JSON):\n"
            "{\n"
            '  "action": string,\n'
            '  "reason": string,\n'
            "}\n"
            f"Allowed actions: {allowed}\n"
            "Constraints:\n"
            f"- action MUST be one of: {allowed}\n"
            "- reason MUST be short and tied to this context.\n"
            "- No extra keys.\n"
        )

    async def run(
        self,
        incoming: Dict[str, Any],
        *,
        actions: Tuple[str, ...] = ("move", "stay"),
        intro: Optional[str] = None,
        intro_fn: Optional[IntroFn] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Returns: {"action": str, "reason": str}
        Raises on invalid JSON or invalid action.
        """
        user_prompt = self._build_user_prompt(
            incoming,
            actions=actions,
            intro=intro,
            intro_fn=intro_fn,
            context=context,
        )

        if self.debug:
            await aprint("\n===== LLM PROMPT (USER) =====\n" + user_prompt + "\n=============================\n")

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )

        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)

        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got: {type(data).__name__}")

        # Enforce exact shape (no extra keys)
        allowed_keys = {"action", "reason", "data"}
        extra = set(data.keys()) - allowed_keys
        if extra:
            raise ValueError(f"Unexpected keys in LLM output: {sorted(extra)}")

        action = data.get("action")
        if action not in actions:
            raise ValueError(f"Invalid action {action!r}. Allowed: {actions}")

        # Parse reason BEFORE any branch uses it
        reason = str(data.get("reason", ""))
        if not reason:
            reason = "No reason provided."

        return {"action": action, "reason": reason}
    
