import asyncio
from typing import Any, Type

# Only the bridge depends on these frameworks.
from crewai import Agent as CrewAgent, Task as CrewTask, Crew
from langchain_openai import ChatOpenAI

class FrameworkBridge:
    """
    A minimal bridge that abstracts the model call:
      - JSON via CrewAI (backed by LangChain ChatOpenAI with JSON response_format)
      - Text via LangChain directly
      - Structured via LangChain with a Pydantic schema
    """

    def __init__(self, *, model: str, max_output_tokens: int) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens

        # LangChain LLMs
        self.lc_json = ChatOpenAI(
            model=self.model,
            temperature=0,
            max_tokens=self.max_output_tokens,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
        self.lc_text = ChatOpenAI(
            model=self.model,
            temperature=0,
            max_tokens=self.max_output_tokens,
        )

        # CrewAI "thin" agent (used only for JSON)
        self._crew_agent = CrewAgent(
            role="Responder",
            goal="Return a strict JSON object answering all questions. Output ONLY JSON.",
            backstory="A stateless structured-output responder.",
            llm=self.lc_json,
            verbose=False,
        )

    async def run_json(self, prompt: str) -> dict:
        """Single-task Crew run that returns strict JSON."""
        task = CrewTask(
            description=prompt,
            agent=self._crew_agent,
            expected_output="A single valid JSON object. No extra text.",
        )
        crew = Crew(agents=[self._crew_agent], tasks=[task], verbose=False)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, crew.kickoff)

        # Normalize to dict
        if isinstance(result, dict):
            return result
        if isinstance(result, list):
            return {"data": result}
        try:
            import json as _json
            return _json.loads(str(result))
        except Exception:
            return {}

    async def run_text(self, prompt: str) -> str:
        """Direct LangChain call that returns plain text."""
        resp = await self.lc_text.ainvoke(prompt)
        return resp.content or ""

    async def run_structured(self, prompt: str, *, output_type: Type) -> Any:
        """
        Direct LangChain call with a Pydantic schema (YourModel).
        Returns either a BaseModel (with .dict()) or a plain dict.
        """
        llm_structured = self.lc_text.with_structured_output(output_type)
        parsed = await llm_structured.ainvoke(prompt)
        return parsed.dict() if hasattr(parsed, "dict") else parsed
