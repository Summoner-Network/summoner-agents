import os
import json
import aiohttp
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Notion Server", json_response=True)

NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION_DEFAULT = "2022-06-28"

def get_notion_token() -> Optional[str]:
    # Supports both for convenience.
    return os.getenv("NOTION_API_KEY") or os.getenv("NOTION_TOKEN")


def build_notion_headers() -> dict[str, str]:
    token = get_notion_token()
    headers: dict[str, str] = {
        "Notion-Version": os.getenv("NOTION_VERSION", NOTION_VERSION_DEFAULT),
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def notion_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> dict:
    url = NOTION_BASE_URL + path
    headers = build_notion_headers()

    async with session.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_body,
    ) as resp:
        text = await resp.text()
        try:
            data = json.loads(text)
        except Exception:
            data = {"raw": text}
        return {"status": resp.status, "data": data}


@mcp.tool()
async def notion_handle_request(
    action: str,
    query: Optional[str] = None,
    database_id: Optional[str] = None,
    block_id: Optional[str] = None,
    page_size: int = 10,
    filter_object: Optional[str] = None,
) -> dict:
    """
    Dispatch Notion operations.
    action: "search" | "database_query" | "block_children"
    """

    token = get_notion_token()
    if not token:
        return {
            "error": "missing_notion_token",
            "details": "Set NOTION_API_KEY or NOTION_TOKEN in the environment.",
            "tool_args": {
                "action": action,
                "query": query,
                "database_id": database_id,
                "block_id": block_id,
                "page_size": page_size,
                "filter_object": filter_object,
            },
        }

    action = (action or "").strip()
    try:
        page_size = int(page_size)
    except Exception:
        page_size = 10
    page_size = max(1, min(page_size, 100))

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if action == "search":
            q = (query or "").strip()
            if not q:
                return {"error": "missing_query_for_search", "tool_args": {"action": action}}

            body: dict = {"query": q, "page_size": page_size}
            fo = (filter_object or "").strip()
            if fo:
                body["filter"] = {"value": fo, "property": "object"}

            resp = await notion_request(session, "POST", "/search", json_body=body)
            return {
                "action": action,
                "query": q,
                "page_size": page_size,
                "status": resp["status"],
                "data": resp["data"],
                "timestamp_utc": timestamp,
            }

        if action == "database_query":
            db = (database_id or "").strip()
            if not db:
                return {"error": "missing_database_id", "tool_args": {"action": action}}

            body = {"page_size": page_size}
            resp = await notion_request(session, "POST", f"/databases/{db}/query", json_body=body)
            return {
                "action": action,
                "database_id": db,
                "page_size": page_size,
                "status": resp["status"],
                "data": resp["data"],
                "timestamp_utc": timestamp,
            }

        if action == "block_children":
            bid = (block_id or "").strip()
            if not bid:
                return {"error": "missing_block_id", "tool_args": {"action": action}}

            params = {"page_size": str(page_size)}
            resp = await notion_request(session, "GET", f"/blocks/{bid}/children", params=params)
            return {
                "action": action,
                "block_id": bid,
                "page_size": page_size,
                "status": resp["status"],
                "data": resp["data"],
                "timestamp_utc": timestamp,
            }

        return {"error": "unsupported_action", "action": action}

# -------------------- main ---------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")  # http://localhost:8000/mcp
