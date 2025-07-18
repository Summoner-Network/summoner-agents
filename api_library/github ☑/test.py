import asyncio
import os
import sys
from dataclasses import dataclass
import aiohttp
from dotenv import load_dotenv

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional

@dataclass
class CommitInfo:
    sha: str
    author: str
    message: str
    url: str
    date: str

async def fetch_commits(session, owner, repo, per_page=30):
    url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page={per_page}"
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
            try:
                commits = await fetch_commits(session, owner, repo)
                if not commits:
                    print("No commits returned.")
                else:
                    # On first run, initialize seen_sha and only print the latest commit
                    if seen_sha is None:
                        seen_sha = commits[0]["sha"]
                        info = commits[0]
                        print(f"[{info['commit']['author']['date']}] "
                              f"Initial commit: {info['sha'][:7]} - "
                              f"{info['commit']['message'].splitlines()[0]}")
                    else:
                        # Collect new commits up until seen_sha
                        new = []
                        for c in commits:
                            if c["sha"] == seen_sha:
                                break
                            new.append(c)
                        if new:
                            # Print in oldest→newest
                            for c in reversed(new):
                                author = c["commit"]["author"]["name"]
                                msg    = c["commit"]["message"].splitlines()[0]
                                date   = c["commit"]["author"]["date"]
                                print(f"[{date}] {author}: {c['sha'][:7]} - {msg}")
                            seen_sha = commits[0]["sha"]
                await asyncio.sleep(interval)
            except Exception as e:
                print("Error monitoring commits:", e)
                await asyncio.sleep(interval)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python github_commit_agent.py <owner> <repo>")
        sys.exit(1)

    owner, repo = sys.argv[1], sys.argv[2]
    print(f"Monitoring commits on {owner}/{repo} every 10s…\n")
    asyncio.run(monitor_commits(owner, repo, interval=10))
