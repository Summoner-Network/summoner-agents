import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import os
import asyncio
import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from dotenv import load_dotenv
from pprint import pprint
from safeguards import (
    count_embedding_tokens,
    estimate_embedding_request_cost,
    actual_embedding_request_cost,
)

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client =  AsyncOpenAI(api_key=API_KEY)
model_ids = [m.id for m in openai.models.list().data]

max_embedding_input_tokens = 500


async def get_embeddings(
    texts: list[str],
    model_name: str = "text-embedding-3-small",
) -> list[list[float]]:
    if model_name not in model_ids:
        raise ValueError(
            f"Invalid model_name '{model_name}'. "
            f"Available models are: {', '.join(model_ids)}"
        )
    # (optional) count & log tokens + estimated cost
    text_tokens = count_embedding_tokens(texts, model_name)
    print(f"Embedding tokens: {text_tokens} > {max_embedding_input_tokens} ? {text_tokens > max_embedding_input_tokens}")

    est_cost = estimate_embedding_request_cost(model_name, text_tokens)
    print(f"[embed] Estimated cost: ${est_cost:.10f}")

    response = await client.embeddings.create(
        model=model_name,
        input=texts,
    )
    pprint(response.usage.to_dict())
    used = response.usage.total_tokens
    act_cost = actual_embedding_request_cost(model_name, used)
    print(f"[embed]  Actual cost:    ${act_cost:.10f}")

    return [record.embedding for record in response.data]



texts = [
    "The quick brown fox jumps over the lazy dog",
    "To be or not to be, that is the question",
]

embs = asyncio.run(get_embeddings(texts))
for i, x in enumerate(embs):
    print(texts[i])
    print(x[:5], len(x))


