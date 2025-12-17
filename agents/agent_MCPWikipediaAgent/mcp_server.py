import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

from typing import Any
from urllib.parse import quote
from datetime import datetime, timezone

import aiohttp

from mcp.server.fastmcp import FastMCP

# -------------------- Wikipedia helpers --------------------

WIKI_SEARCH_BASE  = "https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
WIKI_SUMMARY_BASE = "https://{lang}.wikipedia.org/api/rest_v1/page/summary"

WIKIPEDIA_DEFAULT_HEADERS = {
    "User-Agent": "Summoner-MCPWikipediaAgent/0.1 (bot; contact: you@example.com)",
    "Accept": "application/json",
}

async def _wikipedia_search_titles(
    session: aiohttp.ClientSession,
    query: str,
    *,
    limit: int = 5,
    lang: str = "en",
) -> dict:
    url = WIKI_SEARCH_BASE.format(lang=lang)
    params = {"q": query, "limit": limit}
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        data = await resp.json()

    pages = data.get("pages", []) or []
    results: list[dict] = []
    for p in pages:
        title = p.get("title")
        if not title:
            continue
        desc = p.get("description") or ""
        key = p.get("key") or title
        encoded = quote(title, safe="")
        page_url = f"https://{lang}.wikipedia.org/wiki/{encoded}"
        results.append(
            {
                "title": title,
                "description": desc,
                "key": key,
                "url": page_url,
            }
        )

    return {
        "query": query,
        "lang": lang,
        "limit": limit,
        "count": len(results),
        "pages": results,
    }

async def _wikipedia_summary(
    session: aiohttp.ClientSession,
    title: str,
    *,
    lang: str = "en",
) -> dict:
    encoded = quote(title, safe="")
    url = f"{WIKI_SUMMARY_BASE.format(lang=lang)}/{encoded}"
    async with session.get(url) as resp:
        if resp.status == 404:
            return {
                "error": "page_not_found",
                "title": title,
                "lang": lang,
            }
        resp.raise_for_status()
        data = await resp.json()

    extract = (data.get("extract") or "").strip()
    description = (data.get("description") or "").strip()
    page_title = data.get("title") or title
    content_urls = data.get("content_urls") or {}
    desktop = content_urls.get("desktop") or {}
    page_url = desktop.get("page") or f"https://{lang}.wikipedia.org/wiki/{encoded}"

    if not extract:
        return {
            "error": "no_summary_available",
            "title": page_title,
            "lang": lang,
            "url": page_url,
        }

    return {
        "title": page_title,
        "lang": lang,
        "description": description,
        "summary": extract,
        "url": page_url,
    }

async def _wikipedia_handle_request(tool_args: dict) -> dict:
    action = (tool_args.get("action") or "").strip()
    if not action:
        return {"error": "missing_action", "tool_args": tool_args}

    lang_raw = (tool_args.get("lang") or "en").strip().lower()
    lang = lang_raw or "en"

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    async with aiohttp.ClientSession(headers=WIKIPEDIA_DEFAULT_HEADERS) as session:
        if action == "search_titles":
            query = (tool_args.get("query") or "").strip()
            if not query:
                return {"error": "missing_query", "tool_args": tool_args}

            try:
                limit = int(tool_args.get("limit", 5))
            except Exception:
                limit = 5
            limit = max(1, min(limit, 50))

            core = await _wikipedia_search_titles(session, query, limit=limit, lang=lang)
            core["action"] = "search_titles"
            core["timestamp_utc"] = timestamp
            return core

        if action == "summary":
            title = (tool_args.get("title") or "").strip()
            if not title:
                return {"error": "missing_title", "tool_args": tool_args}

            core = await _wikipedia_summary(session, title, lang=lang)
            core["action"] = "summary"
            core["timestamp_utc"] = timestamp
            return core

        if action == "search_summary":
            query = (tool_args.get("query") or "").strip()
            if not query:
                return {"error": "missing_query", "tool_args": tool_args}

            try:
                limit = int(tool_args.get("limit", 5))
            except Exception:
                limit = 5
            limit = max(1, min(limit, 50))

            search_res = await _wikipedia_search_titles(session, query, limit=limit, lang=lang)
            if not search_res.get("pages"):
                return {
                    "action": "search_summary",
                    "query": query,
                    "lang": lang,
                    "limit": limit,
                    "count": 0,
                    "pages": [],
                    "error": "no_pages_found",
                    "timestamp_utc": timestamp,
                }

            top = search_res["pages"][0]
            summary_res = await _wikipedia_summary(session, top["title"], lang=lang)

            return {
                "action": "search_summary",
                "query": query,
                "lang": lang,
                "limit": limit,
                "search": search_res,
                "top_title": top["title"],
                "summary": summary_res,
                "timestamp_utc": timestamp,
            }

        return {"error": "unsupported_action", "action": action, "tool_args": tool_args}

# -------------------- MCP server --------------------

mcp = FastMCP("wikipedia")

@mcp.tool(name="wikipedia_handle_request")
async def wikipedia_handle_request(tool_args: dict) -> dict:
    return await _wikipedia_handle_request(tool_args)

# -------------------- main --------------------

if __name__ == "__main__":
    # Streamable HTTP MCP endpoint is typically /mcp (as used by your agent).
    mcp.run(transport="streamable-http")

