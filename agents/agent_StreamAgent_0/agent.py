from summoner.client import SummonerClient
from typing import Any, Union, Optional
import argparse
import asyncio
import json
import uuid

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from dotenv import load_dotenv
load_dotenv()


client = SummonerClient(name="StreamAgent_0")

# LLM with streaming enabled
llm = ChatOpenAI(model="gpt-4o-mini", streaming=True)

# Initialized in setup()
token_queue: Optional[asyncio.Queue] = None
current_stream_task: Optional[asyncio.Task] = None


async def setup():
    global token_queue
    token_queue = asyncio.Queue()


async def stream_llm_into_queue(prompt: str, remote_addr: str) -> None:
    """
    Streams tokens from the LLM into token_queue as small payloads.
    """
    assert token_queue is not None

    stream_id = str(uuid.uuid4())

    # Optional: tell the server a stream is starting
    await token_queue.put(
        {"type": "stream_start", "stream_id": stream_id}
    )

    try:
        message = HumanMessage(content=prompt)

        # Preferred: async streaming (does not block the event loop)
        async for chunk in llm.astream([message]):
            if chunk.content:
                await token_queue.put(
                    {
                        "type": "token",
                        "stream_id": stream_id,
                        "token": chunk.content,
                    }
                )

        await token_queue.put(
            {"type": "stream_end", "stream_id": stream_id}
        )

    except asyncio.CancelledError:
        # If a new prompt arrives and we cancel the current stream
        await token_queue.put(
            {"type": "stream_cancelled", "stream_id": stream_id}
        )
        raise

    except Exception as e:
        await token_queue.put(
            {
                "type": "stream_error",
                "stream_id": stream_id,
                "error": str(e),
            }
        )


@client.receive(route="")
async def receiver_handler(msg: Any) -> None:
    """
    Trigger streaming when a message arrives.
    """
    global current_stream_task
    assert token_queue is not None

    # Keep your warning handling
    if isinstance(msg, str) and msg.startswith("Warning:"):
        client.logger.warning(msg.replace("Warning:", "[From Server]"))
        return

    # Expect your server-style envelope:
    # {"remote_addr": "...", "content": ...}
    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        client.logger.info(f"Ignored message (unexpected shape): {type(msg)}")
        return

    remote_addr = str(msg["remote_addr"])
    content = msg["content"]

    # Decide what prompt text is
    # - if content is a string, use it
    # - if content is a dict, try "prompt", else dump json
    if isinstance(content, str):
        prompt = content
    elif isinstance(content, dict):
        prompt = str(content.get("prompt") or json.dumps(content))
    else:
        prompt = str(content)

    client.logger.info(f"Triggering LLM streaming for remote_addr={remote_addr} prompt={prompt!r}")

    # If you want "one active stream at a time", cancel the previous one
    if current_stream_task is not None and not current_stream_task.done():
        client.logger.warning("Cancelling previous stream (new prompt arrived).")
        current_stream_task.cancel()
        try:
            await current_stream_task
        except Exception:
            pass

    current_stream_task = asyncio.create_task(stream_llm_into_queue(prompt, remote_addr))

def get_token_queue() -> asyncio.Queue:
    global token_queue
    if token_queue is None:
        token_queue = asyncio.Queue()
    return token_queue

@client.send(route="")
async def send_handler() -> Union[dict, str, None]:
    q = get_token_queue()
    try:
        return await asyncio.wait_for(q.get(), timeout=0.5)
    except asyncio.TimeoutError:
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the config file (JSON) for the client (e.g., --config configs/client_config.json)')
    args = parser.parse_args()

    client.loop.run_until_complete(setup())

    client.run(host = "127.0.0.1", port = 8888, config_path=args.config_path or "configs/client_config.json")