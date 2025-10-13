import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

import os, json
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

max_chat_input_tokens = 100
max_chat_output_tokens = 1000

async def chat_agent(
        message: str = "Say this is a test", 
        model_name: str = "gpt-4o-mini",
        output_parsing: str = "text",
        output_type = None
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
        if output_parsing == "text":
            response = await client.chat.completions.create(
                                    messages=messages,
                                    model=model_name,
                                    max_completion_tokens=max_chat_output_tokens
                                    )
            pprint(response.usage.to_dict())
            prompt_used  = response.usage.prompt_tokens
            comp_used    = response.usage.completion_tokens
            act_cost     = actual_chat_request_cost(model_name, prompt_used, comp_used)
            print(f"[chat] Actual cost:    ${act_cost:.6f}")
            output = response.choices[0].message.content
        elif output_parsing == "json":
            response = await client.chat.completions.create(
                                    messages=messages,
                                    model=model_name,
                                    max_completion_tokens=max_chat_output_tokens,
                                    response_format={"type": "json_object"}
                                    )
            pprint(response.usage.to_dict())
            prompt_used  = response.usage.prompt_tokens
            comp_used    = response.usage.completion_tokens
            act_cost     = actual_chat_request_cost(model_name, prompt_used, comp_used)
            print(f"[chat] Actual cost:    ${act_cost:.6f}")
            output = json.loads(response.choices[0].message.content)
        elif output_parsing == "structured" and output_type is not None:
            response = await client.responses.parse(
                                    input=messages,
                                    model=model_name, 
                                    max_output_tokens=max_chat_output_tokens,
                                    text_format=output_type
                                    ) 
            # output = response.output[0].content[0].text
            output = response.output[0].content[0].parsed
        else:
            print("Output specification failed -- unable to send the request.")
    else:
        print("Tokens exceeded -- unable to send the request.")
    
    return output


prompt = """What kind of communication protocol is missing for AI agents in a few words? 
Structure your response in a json format using the structure {"number": (int) <number_of_features>, "elements": (list) [items,...]} where an item is a dictionary using the keys 'missing_feature' (str) and 'explanation' (str).                      
"""
print(prompt)

from pydantic import BaseModel
class Feature(BaseModel):
    missing_feature: str
    explanation: str

class Output(BaseModel):
    number: int
    elements: list[Feature]

output = asyncio.run(chat_agent(prompt, output_parsing="structured", output_type=Output))
print("Response:", output)
print()


