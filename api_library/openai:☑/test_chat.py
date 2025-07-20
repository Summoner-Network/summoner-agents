
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
    count_chat_tokens,
    estimate_chat_request_cost,
    actual_chat_request_cost,
)

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client =  AsyncOpenAI(api_key=API_KEY)
model_ids = [m.id for m in openai.models.list().data]

max_chat_output_tokens = 100
max_chat_input_tokens = 100


async def chat_agent(
        message: str = "Say this is a test", 
        model_name: str = "gpt-4o-mini"
) -> ChatCompletion:
    if model_name not in model_ids:
        raise ValueError(
            f"Invalid model_name '{model_name}'. "
            f"Available models are: {', '.join(model_ids)}"
        )
    messages = [
                    {
                        "role": "user",
                        "content": message,
                    }
                ]
    prompt_tokens = count_chat_tokens(messages, model_name)
    print(f"Prompt tokens: {prompt_tokens} > {max_chat_input_tokens} ? {prompt_tokens > max_chat_input_tokens}")
    print(f"Input: {messages}")

    est_cost = estimate_chat_request_cost(model_name, prompt_tokens, max_chat_output_tokens)
    print(f"[chat] Estimated cost: ${est_cost:.6f}")

    if prompt_tokens < max_chat_input_tokens:
        response = await client.chat.completions.create(
                                messages=messages,
                                model=model_name,
                                max_completion_tokens=max_chat_output_tokens,
                                )
    pprint(response.usage.to_dict())
    prompt_used  = response.usage.prompt_tokens
    comp_used    = response.usage.completion_tokens
    act_cost     = actual_chat_request_cost(model_name, prompt_used, comp_used)
    print(f"[chat] Actual cost:    ${act_cost:.6f}")
    
    return response


response = asyncio.run(chat_agent("What kind of communication protocol is missing for AI agents in a few words?"))
print("Response:", response.choices[0].message.content)
print()


