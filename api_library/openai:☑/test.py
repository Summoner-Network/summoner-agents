
import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")
import os
import asyncio
from openai import AsyncOpenAI
from dotenv import load_dotenv
from pprint import pprint
from safeguards import count_chat_tokens

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client =  AsyncOpenAI(api_key=API_KEY)

async def main(message="Say this is a test", model_name = "gpt-4o") -> None:
    messages = [
                    {
                        "role": "user",
                        "content": message,
                    }
                ]
    prompt_tokens = count_chat_tokens(messages, model_name)
    print(f"Prompt tokens: {prompt_tokens} > 100 ? {prompt_tokens > 100}")
    print(f"Input: {messages}")

    if prompt_tokens < 100:
        response = await client.chat.completions.create(
                                messages=messages,
                                model=model_name,
                                max_completion_tokens=100,
                                )
    
    print("Response:", response.choices[0].message.content)
    print()
    pprint(response.usage.to_dict())


asyncio.run(main("What kind of communication protocol is missing for AI agents in a few words?"))