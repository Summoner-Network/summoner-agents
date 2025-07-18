import asyncio
import os
from dataclasses import dataclass
import aiohttp
from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional

@dataclass
class GitHubActivity:
    id: str
    type: str
    repo: str
    created_at: str
    actor: str

async def fetch_events(session, owner, repo=None):
    if repo:
        url = f"https://api.github.com/repos/{owner}/{repo}/events"
    else:
        url = f"https://api.github.com/users/{owner}/events"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    print(f"[DEBUG] GET {url}")
    async with session.get(url, headers=headers) as resp:
        print(f"[DEBUG]   → Status: {resp.status}")
        text = await resp.text()
        print(f"[DEBUG]   → Body (first 500 chars):\n{text[:500]!r}\n")
        resp.raise_for_status()
        return await resp.json()

async def print_new_events(owner, repo=None, poll_interval=10):
    seen = set()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                events = await fetch_events(session, owner, repo)
                if not events:
                    print("[DEBUG] No events in response.")
                new = [e for e in events if e["id"] not in seen]
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
                await asyncio.sleep(poll_interval)
            except Exception as exc:
                print("Error fetching events:", exc)
                await asyncio.sleep(poll_interval)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("owner")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()

    print(f"Monitoring {args.owner}" + (f"/{args.repo}" if args.repo else "") +
          f" every {args.interval}s…\n")
    asyncio.run(print_new_events(args.owner, args.repo, args.interval))
