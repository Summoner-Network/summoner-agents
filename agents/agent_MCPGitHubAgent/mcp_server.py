import os
import aiohttp
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("GitHub Server", json_response=True)

def build_github_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    print(token)
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    return headers

async def github_fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, headers=build_github_headers()) as resp:
        resp.raise_for_status()
        return await resp.json()

@mcp.tool()
async def github_latest_commits_summary(
    owner: str,
    repo: str,
    max_commits: int = 5,
) -> dict:
    owner = (owner or "").strip()
    repo = (repo or "").strip()
    if not owner or not repo:
        return {"owner": owner, "repo": repo, "commits": [], "error": "owner_or_repo_missing"}

    try:
        max_commits = int(max_commits)
    except Exception:
        max_commits = 5
    max_commits = max(1, min(max_commits, 20))

    commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits?per_page={max_commits}"

    timeout = aiohttp.ClientTimeout(total=15)
    summaries: list[dict] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            commits = await github_fetch_json(session, commits_url)
        except Exception as e:
            return {
                "owner": owner,
                "repo": repo,
                "max_commits": max_commits,
                "commits": [],
                "error": f"fetch_commits_failed: {type(e).__name__}: {e}",
            }

        commits_list = [commits] if isinstance(commits, dict) else list(commits or [])
        commits_list = commits_list[:max_commits]

        for c in commits_list:
            sha = c.get("sha")
            if not sha:
                continue

            details_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
            try:
                detail = await github_fetch_json(session, details_url)
            except Exception:
                detail = c

            commit_info = (detail.get("commit") or {})
            author_info = (commit_info.get("author") or {})
            author_name = author_info.get("name") or author_info.get("email")
            date = author_info.get("date")
            message = commit_info.get("message") or ""
            subject = message.splitlines()[0] if message else ""

            files_field = detail.get("files") or []
            if isinstance(files_field, dict):
                files_field = [files_field]

            files_summary = [
                {
                    "filename": f.get("filename"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "changes": f.get("changes"),
                }
                for f in files_field
            ]

            summaries.append(
                {
                    "sha": sha,
                    "short_sha": sha[:7],
                    "author": author_name,
                    "date": date,
                    "subject": subject,
                    "message": message,
                    "html_url": detail.get("html_url"),
                    "stats": detail.get("stats", {}),
                    "files": files_summary,
                }
            )

    return {
        "owner": owner,
        "repo": repo,
        "max_commits": max_commits,
        "count": len(summaries),
        "commits": summaries,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }

# -------------------- main ---------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")  # http://localhost:8000/mcp
