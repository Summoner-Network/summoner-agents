import os
import json
from typing import Any, Callable, Dict, Optional, Sequence

from aioconsole import aprint
from openai import AsyncOpenAI
from dotenv import load_dotenv
load_dotenv()

IntroFn = Callable[..., str]


class LLMClient:
    """
    Extraction-only LLM client.

    Output is a JSON object containing only the allowed field keys.
    Hard rule: if the incoming payload is irrelevant, output {}.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: str = (
            "You extract fields from text. "
            "Never guess. Never invent numbers. "
            "If the input does not contain the requested information, return {}."
        ),
        default_intro: str = "You are a strict information extractor.",
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

    def _build_prompt(
        self,
        incoming: Any,
        *,
        allowed_fields: Sequence[str],
        intro: Optional[str],
        intro_fn: Optional[IntroFn],
        context: Optional[Dict[str, Any]],
    ) -> str:
        context = context or {}

        if intro is None:
            fn = intro_fn or self.intro_fn
            intro = fn(**context) if fn is not None else self.default_intro

        fields_list = ", ".join(allowed_fields)

        return (
            f"{intro}\n\n"
            "Context (authoritative JSON):\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
            "Incoming payload (JSON or text encoded as JSON):\n"
            f"{json.dumps(incoming, ensure_ascii=False, indent=2)}\n\n"
            "Task:\n"
            "Extract ONLY the allowed fields from the incoming payload.\n"
            f"Allowed fields: {fields_list}\n\n"
            "Rules (strict):\n"
            "- Output MUST be a JSON object.\n"
            "- Keys MUST be a subset of allowed fields.\n"
            "- Omit fields you cannot infer with high confidence.\n"
            "- Do NOT output empty strings.\n"
            "- Do NOT invent numbers or booleans.\n"
            "- If the input is irrelevant for these fields, output {}.\n"
            "- No extra keys.\n"
        )

    async def extract(
        self,
        incoming: Any,
        *,
        allowed_fields: Sequence[str],
        intro: Optional[str] = None,
        intro_fn: Optional[IntroFn] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(
            incoming,
            allowed_fields=allowed_fields,
            intro=intro,
            intro_fn=intro_fn,
            context=context,
        )

        if self.debug:
            await aprint("\n===== LLM PROMPT (USER) =====\n" + prompt + "\n=============================\n")

        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )

        text = (resp.choices[0].message.content or "").strip()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got: {type(data).__name__}")

        allowed = set(allowed_fields)
        extra = set(data.keys()) - allowed
        if extra:
            raise ValueError(f"Unexpected keys in LLM output: {sorted(extra)}")

        # Normalize: drop empties
        out: Dict[str, Any] = {}
        for k in allowed_fields:
            if k not in data:
                continue
            v = data.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = v.strip()
                if not v:
                    continue
            out[k] = v

        return out
