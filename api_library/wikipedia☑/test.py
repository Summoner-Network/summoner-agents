import aiohttp
import asyncio
from cachetools import TTLCache
from urllib.parse import quote
from typing import List

WIKI_SEARCH  = "https://en.wikipedia.org/w/rest.php/v1/search/title"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"

WIKIPEDIA_DEFAULT_HEADERS = {
    # Feel free to customize this string for your own project/contact
    "User-Agent": "Summoner-GPTWikipediaAgent/0.1 (bot; contact: you@example.com)",
    "Accept": "application/json",
}

class WikipediaAgent:
    def __init__(self, *, cache_ttl: int = 3600, max_cache: int = 100):
        self._cache   = TTLCache(maxsize=max_cache, ttl=cache_ttl)
        self._session = None  # will be set in __aenter__

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(headers=WIKIPEDIA_DEFAULT_HEADERS)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._session.close()

    async def search(self, query: str, limit: int = 5) -> List[str]:
        """Return top page titles matching the query."""
        params = {"q": query, "limit": limit}
        async with self._session.get(WIKI_SEARCH, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [item["title"] for item in data.get("pages", [])]

    async def summary(self, title: str) -> str:
        """Fetch the introductory summary for a page title, or raise ValueError."""
        key = f"summary:{title}"
        if key in self._cache:
            return self._cache[key]

        encoded = quote(title, safe="")
        url = f"{WIKI_SUMMARY}/{encoded}"
        async with self._session.get(url) as resp:
            if resp.status == 404:
                raise ValueError(f"Page not found: {title}")
            resp.raise_for_status()
            data = await resp.json()

        extract = data.get("extract", "").strip()
        if not extract:
            raise ValueError(f"No summary available for: {title}")

        self._cache[key] = extract
        return extract

    async def get_summary(self, query: str) -> str:
        """
        Search + summarize in one call:
        1) search titles
        2) pick top result
        3) return summary or raise if that fails
        """
        titles = await self.search(query, limit=1)
        if not titles:
            raise ValueError(f"No pages found for: {query}")
        return await self.summary(titles[0])

# Example usage
async def demo():
    async with WikipediaAgent() as agent:
        # 1) Search
        titles = await agent.search("fully homomorphic encryption")
        print("Top matches:", titles)

        # 2) Summarize the first match
        try:
            intro = await agent.summary(titles[0])
            print(f"\nIntro for {titles[0]}:\n{intro}\n")
        except ValueError as e:
            print("Error fetching summary:", e)

        # 3) One-step helper
        try:
            one_step = await agent.get_summary("key switching in homomorphic encryption")
            print("One-step summary:\n", one_step)
        except ValueError as e:
            print("Error in get_summary:", e)

if __name__ == "__main__":
    asyncio.run(demo())
