import asyncio
import asyncpraw
from dotenv import load_dotenv
import os

load_dotenv()

CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
USERNAME = os.getenv("REDDIT_USERNAME")
PW = os.getenv("REDDIT_PW")

# Subreddit to listen to
async def agent():

    reddit = asyncpraw.Reddit(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        username=USERNAME,
        password=PW,
        user_agent='bot by u/SummonerNetwork'
    )
    
    subreddit = await reddit.subreddit("AI_agents")

    max_posts = 1
    count = 0
    async for submission in subreddit.stream.submissions():
        serializable = {
            "title": submission.title,
            "author": str(submission.author),
            "url": submission.url,
            "selftext": submission.selftext,
            "created_utc": submission.created_utc
        }
        print(f"Got new post: {serializable}")
        count += 1
        if count >= max_posts:
            break

    await reddit.close()  # Make sure the session is closed cleanly

asyncio.run(agent())