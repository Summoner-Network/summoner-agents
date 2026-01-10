#!/usr/bin/env python3
import sys
import asyncio
import aiohttp
import xmltodict
from pprint import pprint
from datetime import datetime

BASE_URL = "http://export.arxiv.org/api/query"

async def arxiv_search(session, query, max_results=20):
    """
    Query ArXiv for the given search term.
    Returns a list of entry dicts (Atom XML → Python via xmltodict).
    """
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    async with session.get(BASE_URL, params=params) as resp:
        resp.raise_for_status()
        text = await resp.text()

    # Parse Atom feed
    doc = xmltodict.parse(text)["feed"]
    entries = doc.get("entry", [])
    # If only one result, xmltodict returns a dict, normalize to list
    if isinstance(entries, dict):
        entries = [entries]
    return entries

async def watch_topic(session, topic, seen_map):
    """
    Poll a single topic once:
    - Fetch latest entries
    - Compare against seen_map[topic]
    - For any new entry, print summary + pprint metadata
    """
    entries = await arxiv_search(session, topic)
    if not entries:
        print(f"[{datetime.utcnow().date()}] No results for '{topic}'")
        return

    # Extract arXiv IDs and titles
    latest_id = entries[0]["id"]
    last_seen = seen_map.get(topic)

    # On first run, seed baseline without printing details
    if last_seen is None:
        seen_map[topic] = latest_id
        print(f"[{datetime.utcnow().date()}] Tracking '{topic}'; latest arXiv={latest_id.split('/')[-1]}")
        return

    # Gather newly submitted entries up to last_seen
    new = []
    for e in entries:
        if e["id"] == last_seen:
            break
        new.append(e)

    # For each new entry (oldest first), print and detail
    for e in reversed(new):
        arxiv_id = e["id"].split("/")[-1]
        title    = e["title"].strip().replace("\n", " ")
        published = e["published"][:10]
        print(f"[{published}] arXiv:{arxiv_id} – "{title}"")

        # Build metadata dict
        authors = [a["name"] for a in e.get("author", [])]
        summary = e.get("summary", "").strip()
        snippet = summary[:300] + ("…" if len(summary) > 300 else "")
        pdf_link = next(
            (l["@href"] for l in e.get("link", []) if l["@title"] == "pdf"),
            None
        )

        info = {
            "id": arxiv_id,
            "title": title,
            "authors": authors,
            "published": published,
            "summary_snippet": snippet,
            "pdf_link": pdf_link,
        }
        pprint(info)
        print()

    # Update baseline
    seen_map[topic] = latest_id

async def monitor_topics(topics, interval=60):
    """
    Continuously poll all topics in parallel every `interval` seconds.
    """
    seen_map = {}  # topic → last_seen_arxiv_id
    async with aiohttp.ClientSession() as session:
        print(f"Monitoring {len(topics)} topic(s) every {interval}s:\n  " 
              + ", ".join(topics))
        while True:
            tasks = [watch_topic(session, t, seen_map) for t in topics]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python arxiv_monitor.py <query1> [<query2> ...] [interval_seconds]")
        sys.exit(1)

    *queries, last = sys.argv[1:]
    if last.isdigit():
        interval = int(last)
    else:
        interval = 60
        queries.append(last)

    print(f"Starting ArXiv monitor for: {queries} (interval={interval}s)\n")
    asyncio.run(monitor_topics(queries, interval))
