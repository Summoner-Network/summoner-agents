import os
import asyncio
import threading
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv
from clock_events import good_morning_scheduler

load_dotenv()

BOT_TOKEN   = os.getenv("SLACK_BOT_TOKEN")
APP_TOKEN   = os.getenv("SLACK_APP_TOKEN")

app = App(token=BOT_TOKEN)

@app.event("app_mention")
def handle_mention(body, say):
    event = body["event"]
    user  = event["user"]
    say(f"👋 Hello <@{user}>! I am alive and listening.")

@app.event("message")
def handle_message_events(body, logger):
    logger.info(body)
    print(body)

@app.message("ping")
def handle_ping(message, say):
    say("pong")

def start_scheduler_loop():
    """Run the scheduler in its own asyncio event loop (daemon thread)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Pass the WebClient from Bolt
    loop.run_until_complete(good_morning_scheduler(app.client))

if __name__ == "__main__":
    # 1) Launch scheduler thread
    scheduler_thread = threading.Thread(target=start_scheduler_loop, daemon=True)
    scheduler_thread.start()

    # 2) Start Socket Mode (blocks here)
    handler = SocketModeHandler(app, app_token=APP_TOKEN)
    handler.start()
