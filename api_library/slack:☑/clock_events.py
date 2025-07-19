import asyncio
from datetime import datetime, time
from slack_bolt.app.app import WebClient

async def good_morning_scheduler(client: WebClient):
    sent_today = False
    while True:
        now = datetime.now()
        current_time = now.time()

        # Reset flag after midnight
        if current_time < time(1, 0):
            sent_today = False

        # Between 09:00 and 09:10, send once
        if time(9, 0) <= current_time < time(11, 30) and not sent_today:
            
            await client.chat_postMessage(
                    channel="gm-ga-ge-gn",
                    text= (
                            "🌞 Good morning, everyone! I am the Official SummonerBot on Slack."
                            "\n\n"
                            "I am part of the agent library (`summoner-agents`) that Remy is building :)"
                            )
                    )
            sent_today = True

        # Sleep 10 minutes
        await asyncio.sleep(10 * 60)