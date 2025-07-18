import asyncio
import os
import time
from dataclasses import dataclass
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# Optional: put your GitHub PAT in a .env as GITHUB_TOKEN to raise rate limits
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

@dataclass
class GitHubActivity:
    id: str
    type: str
    repo: str
    created_at: str
    actor: str

async def fetch_events(session, owner, repo=None):
    """
    Fetches the latest events.
    - If repo is None, fetches from /users/{owner}/events (user/org activity)
    - Otherwise, /repos/{owner}/{repo}/events (repo activity)
    """
    if repo:
        url = f"https://api.github.com/repos/{owner}/{repo}/events"
    else:
        url = f"https://api.github.com/users/{owner}/events"
    headers = {
        "Accept": "application/vnd.github+json"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def print_new_events(owner, repo=None, poll_interval=5):
    seen = set()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                events = await fetch_events(session, owner, repo)
                # Filter out events we already printed
                new = [e for e in events if e["id"] not in seen]
                # Print oldest first for readability
                for e in reversed(new):
                    seen.add(e["id"])
                    act = GitHubActivity(
                        id=e["id"],
                        type=e["type"],
                        repo=e["repo"]["name"],
                        created_at=e["created_at"],
                        actor=e["actor"]["login"]
                    )
                    print(f"[{act.created_at}] {act.actor} → {act.repo}: {act.type}")
                # Wait for the next poll
                await asyncio.sleep(poll_interval)
            except Exception as exc:
                print("Error fetching events:", exc)
                await asyncio.sleep(poll_interval)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="GitHub Activity Monitor"
    )
    parser.add_argument(
        "owner",
        help="GitHub username or organization"
    )
    parser.add_argument(
        "--repo",
        help="(Optional) specific repository name under the owner",
        default=None
    )
    parser.add_argument(
        "--interval",
        help="Polling interval in seconds (default: 60)",
        type=int,
        default=60
    )
    args = parser.parse_args()

    print(f"Monitoring GitHub {'repo' if args.repo else 'user'} "
          f"‘{args.owner}'{('/' + args.repo) if args.repo else ''}"
          f" every {args.interval}s…\n")
    asyncio.run(print_new_events(args.owner, args.repo, args.interval))
