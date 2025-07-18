#!/usr/bin/env python3
import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from pprint import pprint
from dotenv import load_dotenv
import argparse

load_dotenv()
TOKEN = os.getenv("GITHUB_TOKEN")  # optional for higher rate limits

API_HEADERS = {
    "Accept": "application/vnd.github+json",
    **({"Authorization": f"token {TOKEN}"} if TOKEN else {}),
}


async def fetch_json(session, url):
    async with session.get(url, headers=API_HEADERS) as resp:
        resp.raise_for_status()
        return await resp.json()


async def list_repos(session, owner):
    """Fetch all repository names under an owner."""
    repos, page = [], 1
    while True:
        batch = await fetch_json(
            session,
            f"https://api.github.com/users/{owner}/repos?per_page=100&page={page}"
        )
        if not batch:
            break
        repos.extend(r["name"] for r in batch)
        page += 1
    return repos


async def fetch_commits(session, owner, repo, per_page=30):
    return await fetch_json(
        session,
        f"https://api.github.com/repos/{owner}/{repo}/commits?per_page={per_page}"
    )


async def fetch_commit_details(session, owner, repo, sha):
    return await fetch_json(
        session,
        f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
    )


async def watch_repo(session, owner, repo, seen_map):
    """Check one repo for new commits and print them."""
    seen_sha = seen_map.get(repo)
    commits = await fetch_commits(session, owner, repo)
    if not commits:
        return

    # On first ever poll, record baseline and announce last commit
    if seen_sha is None:
        seen_map[repo] = commits[0]["sha"]
        info = commits[0]["commit"]
        ts = info["author"]["date"]
        subj = info["message"].splitlines()[0]
        print(f"[{ts}] {owner}/{repo} ▶ Last commit: "
              f"{commits[0]['sha'][:7]} – {subj}")
        return

    # Otherwise, collect any newer commits
    new = []
    for c in commits:
        if c["sha"] == seen_sha:
            break
        new.append(c)

    # Print each new commit (oldest first)
    for c in reversed(new):
        sha = c["sha"]
        detail = await fetch_commit_details(session, owner, repo, sha)
        author  = detail["commit"]["author"]["name"]
        date    = detail["commit"]["author"]["date"]
        subject = detail["commit"]["message"].splitlines()[0]

        # one-line summary
        print(f"[{date}] {owner}/{repo} ▶ {author}: "
              f"{sha[:7]} – {subject}")

        # full metadata as JSON-like dict
        info = {
            "owner":  owner,
            "repo":   repo,
            "sha":    sha,
            "author": author,
            "date":   date,
            "message": detail["commit"]["message"],
            "url":    detail["html_url"],
            "stats":  detail.get("stats", {}),
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
        print()

    # update baseline
    seen_map[repo] = commits[0]["sha"]


async def monitor(owner, repos, interval):
    """
    Polls either a single repo or multiple repos under an owner.
    `repos` is a list of repo names to watch.
    """
    seen_map = {repo: None for repo in repos}
    async with aiohttp.ClientSession() as session:
        print(f"Watching {len(repos)} repo(s) under '{owner}' "
              f"every {interval}s:\n  " + ", ".join(repos))
        while True:
            tasks = [watch_repo(session, owner, r, seen_map) for r in repos]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(
        description="Monitor GitHub commits for an owner/repo"
    )
    parser.add_argument("owner", help="GitHub username or organization")
    parser.add_argument(
        "--repo",
        help="Specific repository name (omit to watch all repos under owner)",
        default=None
    )
    parser.add_argument(
        "--interval",
        type=int,
        help="Polling interval in seconds (default: 10)",
        default=10
    )
    args = parser.parse_args()

    # Determine which repos to monitor
    async def runner():
        async with aiohttp.ClientSession() as session:
            if args.repo:
                repos = [args.repo]
            else:
                repos = await list_repos(session, args.owner)
                if not repos:
                    print(f"No repositories found for owner '{args.owner}'.")
                    return
        await monitor(args.owner, repos, args.interval)

    print(f"Starting GitHub commit monitor for '{args.owner}' "
          f"{('/' + args.repo) if args.repo else ''} "
          f"every {args.interval}s…\n")
    asyncio.run(runner())


if __name__ == "__main__":
    main()
