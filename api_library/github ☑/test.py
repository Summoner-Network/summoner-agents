from pprint import pprint
import asyncio
import os
import sys
import aiohttp
from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional, for higher rate limits

async def fetch_commits(session, owner, repo, per_page=30):
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page={per_page}"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def fetch_commit_details(session, owner, repo, sha):
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    async with session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.json()

async def monitor_commits(owner, repo, interval=10):
    seen_sha = None
    async with aiohttp.ClientSession() as session:
        while True:
            commits = await fetch_commits(session, owner, repo)
            if not commits:
                print("No commits returned.")
            else:
                # First run: set the baseline to the latest commit
                if seen_sha is None:
                    seen_sha = commits[0]["sha"]
                    print(f"Last seen commit: {seen_sha[:7]}")
                else:
                    # Gather any commits newer than our last seen SHA
                    new = []
                    for c in commits:
                        if c["sha"] == seen_sha:
                            break
                        new.append(c)

                    # For each new commit (oldest first), fetch details and pprint
                    for c in reversed(new):
                        sha = c["sha"]
                        detail = await fetch_commit_details(session, owner, repo, sha)

                        info = {
                            "sha": sha,
                            "author": detail["commit"]["author"]["name"],
                            "date": detail["commit"]["author"]["date"],
                            "message": detail["commit"]["message"].splitlines()[0],
                            "url": detail["html_url"],
                            "stats": detail.get("stats", {}),
                            "files": [
                                {
                                    "filename": f["filename"],
                                    "additions": f["additions"],
                                    "deletions": f["deletions"],
                                    "changes": f["changes"]
                                }
                                for f in detail.get("files", [])
                            ]
                        }
                        pprint(info)
                    # Update baseline to the most recent commit
                    seen_sha = commits[0]["sha"]

            await asyncio.sleep(interval)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python github_commit_agent.py <owner> <repo>")
        sys.exit(1)

    owner, repo = sys.argv[1], sys.argv[2]
    print(f"Monitoring commits on {owner}/{repo} every 10s…\n")
    asyncio.run(monitor_commits(owner, repo, interval=10))
