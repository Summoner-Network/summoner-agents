# mcp_server.py
import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

from typing import Any, Optional
from datetime import datetime, timezone

import aiohttp
import xmltodict

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("summoner-mcp-arxiv")

# -------------------- ArXiv helpers (ported from agent) ---------------------

ARXIV_BASE_URL = "http://export.arxiv.org/api/query"


async def arxiv_search_raw(query: str, max_results: int = 20) -> list[dict]:
    """
    One-shot ArXiv search.
    Returns a list of entry dicts (Atom XML -> Python via xmltodict).
    """
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(ARXIV_BASE_URL, params=params) as resp:
            resp.raise_for_status()
            text = await resp.text()

    doc = xmltodict.parse(text)["feed"]
    entries = doc.get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    return entries


def arxiv_entry_to_summary(e: dict) -> dict:
    """
    Normalize a single ArXiv entry to a compact JSON-friendly summary.
    """
    arxiv_id = e["id"].split("/")[-1]
    title = e["title"].strip().replace("\n", " ")
    published = e["published"][:10]

    authors_field = e.get("author", [])
    if isinstance(authors_field, dict):
        authors = [authors_field.get("name")]
    else:
        authors = [a.get("name") for a in authors_field]

    summary = (e.get("summary") or "").strip()
    snippet = summary[:300] + ("…" if len(summary) > 300 else "")

    links = e.get("link", [])
    if isinstance(links, dict):
        links = [links]
    pdf_link = None
    for l in links:
        if l.get("@title") == "pdf":
            pdf_link = l.get("@href")
            break

    return {
        "id": arxiv_id,
        "title": title,
        "authors": authors,
        "published": published,
        "summary_snippet": snippet,
        "pdf_link": pdf_link,
    }


async def arxiv_search_summaries(query: str, max_results: int = 5) -> dict:
    """
    High level helper used by the MCP tool.
    Returns a dict with metadata and a list of normalized entries.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "empty_query"}

    try:
        max_results = int(max_results)
    except Exception:
        max_results = 5

    max_results = max(1, min(max_results, 50))

    entries = await arxiv_search_raw(query=query, max_results=max_results)
    summaries = [arxiv_entry_to_summary(e) for e in entries]

    return {
        "query": query,
        "max_results": max_results,
        "count": len(summaries),
        "results": summaries,
        "timestamp_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }


# -------------------- MCP tool ---------------------

@mcp.tool()
async def arxiv_handle_request(tool_args: dict) -> dict:
    """
    MCP tool called by MCPArXivAgent.

    Expected tool_args keys:
      - query (required)
      - max_results (optional: 1..50)
    """
    query = (tool_args.get("query") or "").strip()
    if not query:
        return {"error": "missing_query", "tool_args": tool_args}

    max_results: Optional[int] = tool_args.get("max_results", 5)

    try:
        max_results_int = int(max_results)  # type: ignore[arg-type]
    except Exception:
        max_results_int = 5

    max_results_int = max(1, min(max_results_int, 50))
    return await arxiv_search_summaries(query=query, max_results=max_results_int)


# -------------------- main ---------------------

if __name__ == "__main__":
    mcp.run(transport="streamable-http")     # http://localhost:8000/mcp
