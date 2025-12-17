import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

from typing import Any, Optional
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
import asyncpraw

from mcp.server.fastmcp import FastMCP

load_dotenv()

# -------------------- MCP server --------------------

mcp = FastMCP("Reddit MCP Server", json_response=True)

# -------------------- Reddit helpers --------------------

def _get_reddit_credentials() -> tuple[Optional[dict], Optional[list[str]]]:
    """
    Load Reddit credentials from environment.

    Required:
      - REDDIT_CLIENT_ID
      - REDDIT_CLIENT_SECRET
      - REDDIT_USERNAME
      - REDDIT_PW

    Optional:
      - REDDIT_USER_AGENT
    """
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PW")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    missing: list[str] = []
    if not client_id:
        missing.append("REDDIT_CLIENT_ID")
    if not client_secret:
        missing.append("REDDIT_CLIENT_SECRET")
    if not username:
        missing.append("REDDIT_USERNAME")
    if not password:
        missing.append("REDDIT_PW")

    if missing:
        return None, missing

    if not user_agent:
        user_agent = f"summoner-reddit-bot by u/{username}"

    creds = {
        "client_id": client_id,
        "client_secret": client_secret,
        "username": username,
        "password": password,
        "user_agent": user_agent,
    }
    return creds, None


def _serialize_submission(submission: Any) -> dict:
    """Convert a PRAW submission into a simple dict."""
    snippet = (submission.selftext or "").strip()
    if len(snippet) > 300:
        snippet = snippet[:300] + "…"

    return {
        "id": submission.id,
        "title": submission.title,
        "subreddit": str(submission.subreddit),
        "author": str(submission.author) if submission.author else None,
        "score": submission.score,
        "num_comments": submission.num_comments,
        "created_utc": submission.created_utc,
        "url": submission.url,
        "permalink": f"https://www.reddit.com{submission.permalink}",
        "selftext_snippet": snippet,
    }


def _serialize_comment(comment: Any) -> dict:
    """Convert a PRAW comment into a simple dict."""
    return {
        "id": comment.id,
        "author": str(comment.author) if comment.author else None,
        "score": comment.score,
        "created_utc": comment.created_utc,
        "body": comment.body,
        "permalink": f"https://www.reddit.com{comment.permalink}",
    }


async def _reddit_subreddit_posts(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'subreddit_posts'

    Expected tool_args keys:
      - subreddit (required)
      - sort (optional: 'hot'|'new'|'top'|'rising'|'controversial')
      - limit (optional: 1..50, default 10)
      - query (optional: if present, perform a subreddit search instead of raw listing)
    """
    raw_subreddit = (tool_args.get("subreddit") or "").strip()
    if raw_subreddit.lower().startswith("r/"):
        raw_subreddit = raw_subreddit[2:]
    subreddit_name = raw_subreddit

    if not subreddit_name:
        return {
            "error": "missing_subreddit",
            "tool_args": tool_args,
        }

    sort_raw = (tool_args.get("sort") or "hot").strip().lower()
    allowed_sorts = {"hot", "new", "top", "rising", "controversial"}
    sort = sort_raw if sort_raw in allowed_sorts else "hot"

    try:
        limit = int(tool_args.get("limit", 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    query = (tool_args.get("query") or "").strip() or None

    sub = await reddit.subreddit(subreddit_name)

    posts: list[dict] = []
    if query:
        async for s in sub.search(
            query=query,
            sort="relevance",
            time_filter="all",
            limit=limit,
        ):
            posts.append(_serialize_submission(s))
    else:
        listing = getattr(sub, sort, sub.hot)
        async for s in listing(limit=limit):
            posts.append(_serialize_submission(s))

    return {
        "subreddit": subreddit_name,
        "sort": sort,
        "limit": limit,
        "query": query,
        "count": len(posts),
        "posts": posts,
    }


async def _reddit_search(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'search'

    Expected tool_args keys:
      - query (required)
      - subreddit (optional: 'all' or a specific subreddit)
      - sort (optional: 'relevance'|'hot'|'new'|'top'|'comments')
      - time_filter (optional: 'all'|'hour'|'day'|'week'|'month'|'year')
      - limit (optional: 1..50, default 10)
    """
    query = (tool_args.get("query") or "").strip()
    if not query:
        return {
            "error": "missing_query",
            "tool_args": tool_args,
        }

    raw_subreddit = (tool_args.get("subreddit") or "").strip()
    if raw_subreddit.lower().startswith("r/"):
        raw_subreddit = raw_subreddit[2:]
    subreddit_name = raw_subreddit or "all"

    sort_raw = (tool_args.get("sort") or "relevance").strip().lower()
    allowed_sorts = {"relevance", "hot", "new", "top", "comments"}
    sort = sort_raw if sort_raw in allowed_sorts else "relevance"

    time_raw = (tool_args.get("time_filter") or "").strip().lower()
    allowed_time = {"all", "hour", "day", "week", "month", "year"}
    time_filter = time_raw if time_raw in allowed_time else "month"

    try:
        limit = int(tool_args.get("limit", 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    sub = await reddit.subreddit(subreddit_name)

    posts: list[dict] = []
    async for s in sub.search(
        query=query,
        sort=sort,
        time_filter=time_filter,
        limit=limit,
    ):
        posts.append(_serialize_submission(s))

    return {
        "query": query,
        "subreddit": subreddit_name,
        "sort": sort,
        "time_filter": time_filter,
        "limit": limit,
        "count": len(posts),
        "posts": posts,
    }


async def _reddit_comments(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'comments'

    Expected tool_args keys:
      - submission_id (optional)
      - submission_url (optional)
      - sort (optional: 'top'|'new'|'controversial'|'old'|'qa')
      - limit (optional: number of comments after flattening, default 20)
    """
    submission_url = (tool_args.get("submission_url") or "").strip()
    submission_id = (tool_args.get("submission_id") or "").strip()

    if not submission_url and not submission_id:
        return {
            "error": "missing_submission_reference",
            "tool_args": tool_args,
        }

    sort_raw = (tool_args.get("sort") or "top").strip().lower()
    allowed_sorts = {"top", "new", "controversial", "old", "qa"}
    sort = sort_raw if sort_raw in allowed_sorts else "top"

    try:
        limit = int(tool_args.get("limit", 20))
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))

    if submission_url:
        submission = await reddit.submission(url=submission_url)
    else:
        submission = await reddit.submission(id=submission_id)

    submission.comment_sort = sort
    await submission.comments.replace_more(limit=0)
    all_comments = submission.comments.list()

    comments: list[dict] = []
    for c in all_comments[:limit]:
        comments.append(_serialize_comment(c))

    return {
        "submission_id": submission.id,
        "submission_url": f"https://www.reddit.com{submission.permalink}",
        "sort": sort,
        "limit": limit,
        "count": len(comments),
        "comments": comments,
    }

# -------------------- MCP tool --------------------

@mcp.tool()
async def reddit_handle_request(tool_args: dict) -> dict:
    """
    High-level helper used by the MCP tool.

    Supported actions:
      - 'subreddit_posts'
      - 'search'
      - 'comments'
    """
    action = (tool_args.get("action") or "").strip()
    if not action:
        return {
            "error": "missing_action",
            "tool_args": tool_args,
        }

    creds, missing = _get_reddit_credentials()
    if missing:
        return {
            "error": "missing_reddit_credentials",
            "details": f"Missing environment variables: {', '.join(missing)}",
            "tool_args": tool_args,
        }

    reddit = asyncpraw.Reddit(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        username=creds["username"],
        password=creds["password"],
        user_agent=creds["user_agent"],
    )

    try:
        if action == "subreddit_posts":
            core_result = await _reddit_subreddit_posts(reddit, tool_args)
        elif action == "search":
            core_result = await _reddit_search(reddit, tool_args)
        elif action == "comments":
            core_result = await _reddit_comments(reddit, tool_args)
        else:
            return {
                "error": "unsupported_action",
                "action": action,
                "tool_args": tool_args,
            }
    finally:
        await reddit.close()

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    result: dict[str, Any] = {
        "action": action,
        "timestamp_utc": timestamp,
    }
    result.update(core_result)
    return result

# -------------------- main --------------------

if __name__ == "__main__":
    # Streamable HTTP MCP endpoint is typically /mcp (as used by your agent).
    mcp.run(transport="streamable-http")
