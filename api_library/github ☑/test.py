import os
import sys
import aiohttp
import asyncio
from dotenv import load_dotenv
from datetime import datetime
from pprint import pprint


load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")  # optional for higher rate limits

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
                # First run: set baseline to latest commit, print “Last commit”
                if seen_sha is None:
                    seen_sha = commits[0]["sha"]
                    info = commits[0]["commit"]
                    ts = info["author"]["date"]
                    msg = info["message"].splitlines()[0]
                    print(f"[{ts}] Last commit: {seen_sha[:7]} - {msg}")
                else:
                    # Gather any commits newer than seen_sha
                    new = []
                    for c in commits:
                        if c["sha"] == seen_sha:
                            break
                        new.append(c)

                    # For each new commit (oldest first) print summary + pprint details
                    for c in reversed(new):
                        sha = c["sha"]
                        detail = await fetch_commit_details(session, owner, repo, sha)

                        # Summary line
                        author = detail["commit"]["author"]["name"]
                        date   = detail["commit"]["author"]["date"]
                        subject = detail["commit"]["message"].splitlines()[0]
                        print(f"[{date}] {author}: {sha[:7]} - {subject}")

                        # Full JSON-style dict
                        info = {
                            "sha": sha,
                            "author": author,
                            "date": date,
                            "message": detail["commit"]["message"],
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
                        print()  # blank line for readability

                    seen_sha = commits[0]["sha"]

            await asyncio.sleep(interval)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python github_commit_agent.py <owner> <repo>")
        sys.exit(1)

    owner, repo = sys.argv[1], sys.argv[2]
    print(f"Monitoring commits on {owner}/{repo} every 10s…\n")
    asyncio.run(monitor_commits(owner, repo, interval=10))
