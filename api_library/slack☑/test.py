import os
import asyncio

from dotenv import load_dotenv

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")   # xoxb-...
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")   # xapp-...

async def handle_events(sm_client: SocketModeClient, req: SocketModeRequest):
    """
    Main handler for everything Slack sends over Socket Mode.
    """
    # 1) ACK the envelope so Slack doesn't retry
    await sm_client.send_socket_mode_response(
        SocketModeResponse(envelope_id=req.envelope_id)
    )

    # We only care about "events_api" envelopes here
    if req.type != "events_api":
        return

    event = req.payload.get("event", {}) or {}
    event_type = event.get("type")

    # --- Detect app mentions ---
    if event_type == "app_mention":
        channel = event.get("channel")
        user    = event.get("user")

        # Simple hello reply to the user
        if channel and user:
            await sm_client.web_client.chat_postMessage(
                channel=channel,
                text=f"👋 Hello <@{user}>! I heard your mention via slack_sdk + Socket Mode."
            )
        return

    # --- You can also handle plain messages here if you want ---
    if event_type == "message" and not event.get("bot_id"):
        text    = (event.get("text") or "").strip().lower()
        channel = event.get("channel")
        user    = event.get("user")

        print(f"[message] user={user} channel={channel} text={text!r}")

        # Example: simple ping/pong test
        if text == "ping" and channel and user:
            await sm_client.web_client.chat_postMessage(
                channel=channel,
                text=f"pong (from slack_sdk Socket Mode)"
            )

async def main():
    # Async WebClient for calling Slack Web API (chat_postMessage, etc.)
    web_client = AsyncWebClient(token=SLACK_BOT_TOKEN)

    # Socket Mode client for receiving events
    sm_client = SocketModeClient(
        app_token=SLACK_APP_TOKEN,
        web_client=web_client,
    )

    # Register our event handler
    sm_client.socket_mode_request_listeners.append(handle_events)

    # Connect and keep the process alive
    await sm_client.connect()
    print("Socket Mode client connected. Waiting for events...")
    await asyncio.Event().wait()  # never completes; keeps the loop alive

if __name__ == "__main__":
    asyncio.run(main())
