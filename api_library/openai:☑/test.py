import os
import asyncio
from openai import DefaultAioHttpClient
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")

client =  AsyncOpenAI(api_key=API_KEY)

async def main() -> None:
    response = await client.chat.completions.create(
                            messages=
                            [
                                {
                                    "role": "user",
                                    "content": "Say this is a test",
                                }
                            ],
                            model="gpt-4o",
                            )
    print(response)



asyncio.run(main())