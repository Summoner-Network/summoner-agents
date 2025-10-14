import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import os
import asyncio
from typing import Optional, Any

import openai
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pprint import pprint

from safeguards import (
    count_embedding_tokens,
    estimate_embedding_request_cost,
    actual_embedding_request_cost,
    get_usage_from_response,
)

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client = AsyncOpenAI(api_key=API_KEY)
model_ids = [m.id for m in openai.models.list().data]

max_embedding_input_tokens = 500


async def get_embeddings(
    texts: list[str],
    model_name: str = "text-embedding-3-small",
    cost_limit: Optional[float] = None,
    debug: bool = False,
) -> dict[str, Any]:
    """
    Compute embeddings for a list of texts with simple budgeting/diagnostics.

    Returns:
        {"output": list[list[float]] | None, "cost": float | None}
    """
    if model_name not in model_ids:
        raise ValueError(
            f"Invalid model_name '{model_name}'. "
            f"Available models are: {', '.join(model_ids)}"
        )

    text_tokens = count_embedding_tokens(texts, model_name)
    if debug:
        print(
            f"\033[96mEmbedding tokens: {text_tokens} > {max_embedding_input_tokens} ? "
            f"{text_tokens > max_embedding_input_tokens}\033[0m"
        )

    est_cost = estimate_embedding_request_cost(model_name, text_tokens)
    if debug:
        print(f"\033[95m[embed] Estimated cost: ${est_cost:.10f}\033[0m")

    # Guard 1: token ceiling (simple stop)
    if text_tokens > max_embedding_input_tokens:
        if debug:
            print("\033[93m[embed] Tokens exceeded — unable to send the request.\033[0m")
        return {"output": None, "cost": None}

    # Guard 2: cost ceiling (compare estimated cost to limit)
    if cost_limit is not None and est_cost > cost_limit:
        if debug:
            print(
                f"\033[93m[embed] Skipping request: estimated cost ${est_cost:.10f} "
                f"exceeds cost_limit ${cost_limit:.10f}.\033[0m"
            )
        return {"output": None, "cost": None}

    # Call embeddings API
    response = await client.embeddings.create(
        model=model_name,
        input=texts,
    )

    # Usage & cost (SDKs usually expose prompt/total for embeddings)
    usage = get_usage_from_response(response)
    act_cost = None
    if usage:
        if debug:
            pprint(usage.to_dict())
        # Embeddings bill input-only; using total_tokens here is fine.
        act_cost = actual_embedding_request_cost(model_name, usage.total_tokens)
        if debug:
            print(f"\033[95m[embed] Actual cost: ${act_cost:.10f}\033[0m")
    else:
        if debug:
            print("\033[93m[embed] Note: usage not available. Skipping cost.\033[0m")

    return {"output": [record.embedding for record in response.data], "cost": act_cost}


texts = [
    "The quick brown fox jumps over the lazy dog",
    "To be or not to be, that is the question",
]
embs = asyncio.run(get_embeddings(texts, debug=True))
print(f"cost: ${embs['cost']}")
for i, x in enumerate(embs["output"] or []):
    print(texts[i])
    print(x[:5], len(x))
