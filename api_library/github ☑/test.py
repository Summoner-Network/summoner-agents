import asyncio
import os
from dataclasses import dataclass
import aiohttp
from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional, for higher rate limits

@dataclass
class GitHubActivity:
    id: str
    type: str
    repo: str
    created_at: str
    actor: str

async def fetch_events(session, owner, repo=None):
    if repo:
        url = f"https://api.github.com/repos/{owner}/{repo}/events?per_page=100"
    else:
        url = f"https://api.github.com/users/{owner}/events?per_page=100"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def print_new_events(owner, repo=None, poll_interval=60):
    seen = set()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                events = await fetch_events(session, owner, repo)
                if not events:
                    print("[DEBUG] No events returned.")
                else:
                    # Debug dump of the first few event types
                    types = [e["type"] for e in events[:5]]
                    print(f"[DEBUG] Fetched {len(events)} events. Types: {types}")

                # Filter for PushEvents
                pushes = [e for e in events if e["type"] == "PushEvent" and e["id"] not in seen]
                for e in reversed(pushes):  # oldest first
                    seen.add(e["id"])
                    act = GitHubActivity(
                        id=e["id"],
                        type=e["type"],
                        repo=e["repo"]["name"],
                        created_at=e["created_at"],
                        actor=e["actor"]["login"]
                    )
                    print(f"[{act.created_at}] {act.actor} → {act.repo}: {act.type}")
                await asyncio.sleep(poll_interval)
            except Exception as exc:
                print("Error fetching events:", exc)
                await asyncio.sleep(poll_interval)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GitHub PushEvent Monitor")
    parser.add_argument("owner", help="GitHub org/user")
    parser.add_argument("--repo", help="Repository name (optional)", default=None)
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    args = parser.parse_args()

    target = f"{args.owner}/{args.repo}" if args.repo else args.owner
    print(f"Monitoring PushEvents on {target} every {args.interval}s…\n")
    asyncio.run(print_new_events(args.owner, args.repo, args.interval))
