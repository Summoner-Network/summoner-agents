import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import os
import json
import asyncio
from typing import Any, Optional, Type, Literal

import openai
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pprint import pprint

from safeguards import (
    count_chat_tokens,
    estimate_chat_request_cost,
    actual_chat_request_cost,
    get_usage_from_response,
)

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client = AsyncOpenAI(api_key=API_KEY)
model_ids = [m.id for m in openai.models.list().data]

max_chat_input_tokens = 100
max_chat_output_tokens = 1000


async def chat_agent(
    message: str = "Say this is a test",
    model_name: str = "gpt-4o-mini",
    output_parsing: Literal["text", "json", "structured"] = "text",
    output_type: Optional[Type] = None,
    cost_limit: Optional[float] = None,
    debug: bool = False,
) -> dict[str, Any]:
    """
    Send a single-turn prompt and parse output according to `output_parsing`.

    Args:
        message: User content.
        model_name: Target model ID (validated against current list).
        output_parsing: One of "text" | "json" | "structured".
        output_type: Required when output_parsing="structured" (e.g., a Pydantic model class).
        cost_limit: If provided, skip the request when estimated cost exceeds this USD threshold.
        debug: If True, prints token/cost diagnostics.

    Returns:
        {"output": <parsed result or None>, "cost": <actual USD cost or None>}
    """
    if model_name not in model_ids:
        raise ValueError(
            f"Invalid model_name '{model_name}'. "
            f"Available models are: {', '.join(model_ids)}"
        )

    messages: list[dict[str, str]] = [{"role": "user", "content": message}]

    prompt_tokens = count_chat_tokens(messages, model_name)
    if debug:
        print(
            f"\033[96mPrompt tokens: {prompt_tokens} > {max_chat_input_tokens}? "
            f"{prompt_tokens > max_chat_input_tokens}\033[0m"
        )
        print(f"\033[92mInput: {messages}\033[0m")

    est_cost = estimate_chat_request_cost(model_name, prompt_tokens, max_chat_output_tokens)
    if debug:
        print(
            f"\033[95m[chat] Estimated cost (for {max_chat_output_tokens} output tokens): "
            f"${est_cost:.6f}\033[0m"
        )

    output: Any = None
    act_cost: Optional[float] = None

    # Guard 1: token ceiling (simple stop)
    if prompt_tokens >= max_chat_input_tokens:
        if debug:
            print("\033[93mTokens exceeded — unable to send the request.\033[0m")
        return {"output": output, "cost": act_cost}

    # Guard 2: cost ceiling (compare estimated cost to limit)
    if cost_limit is not None and est_cost > cost_limit:
        if debug:
            print(
                f"\033[93m[chat] Skipping request: estimated cost ${est_cost:.6f} "
                f"exceeds cost_limit ${cost_limit:.6f}.\033[0m"
            )
        return {"output": output, "cost": act_cost}

    # Proceed with the call
    if output_parsing == "text":
        response = await client.chat.completions.create(
            messages=messages,
            model=model_name,
            max_completion_tokens=max_chat_output_tokens,
        )
        usage = get_usage_from_response(response)
        if usage:
            if debug:
                pprint(usage.to_dict())
            act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
            if debug:
                print(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
        else:
            if debug:
                print("\033[93m[chat] Note: usage not available. Skipping cost.\033[0m")
        output = response.choices[0].message.content

    elif output_parsing == "json":
        response = await client.chat.completions.create(
            messages=messages,
            model=model_name,
            max_completion_tokens=max_chat_output_tokens,
            response_format={"type": "json_object"},
        )
        usage = get_usage_from_response(response)
        if usage:
            if debug:
                pprint(usage.to_dict())
            act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
            if debug:
                print(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
        else:
            if debug:
                print("\033[93m[chat] Note: usage not available. Skipping cost.\033[0m")
        output = json.loads(response.choices[0].message.content)

    elif output_parsing == "structured":
        if output_type is None:
            raise ValueError("output_type (schema) is required when output_parsing='structured'.")
        response = await client.responses.parse(
            input=messages,
            model=model_name,
            max_output_tokens=max_chat_output_tokens,
            text_format=output_type,
        )
        usage = get_usage_from_response(response)
        if usage:
            if debug:
                pprint(usage.to_dict())
            act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
            if debug:
                print(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
        else:
            if debug:
                print("\033[93m[chat] Note: usage not available for structured response. Skipping cost.\033[0m")

        # For a raw dictionary you could also use:
        #   output = response.output[0].content[0].text
        output = response.output[0].content[0].parsed

    else:
        # Unknown parsing mode: fail fast (previously only printed if debug=True)
        raise ValueError(f"Unrecognized output_parsing: {output_parsing!r}")

    return {"output": output, "cost": act_cost}


# Example usage
prompt = (
    "What kind of communication protocol is missing for AI agents in a few words?\n"
    'Structure your response in a json format using the structure '
    '{"number": (int) <number_of_features>, "elements": (list) [items,...]} '
    "where an item is a dictionary using the keys 'missing_feature' (str) and 'explanation' (str)."
)
print(prompt)

from pydantic import BaseModel

class Feature(BaseModel):
    missing_feature: str
    explanation: str

class Output(BaseModel):
    number: int
    elements: list[Feature]

result = asyncio.run(
    chat_agent(
        prompt,
        output_parsing="structured",   # try "text", "json" or "structured"
        output_type=Output,      # only needed for "structured"
        cost_limit=None,         # e.g., 0.0005 to guard
        debug=True,
    )
)
print("Response:")
pprint(result)
