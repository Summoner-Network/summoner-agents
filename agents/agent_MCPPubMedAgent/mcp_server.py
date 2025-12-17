import os
import aiohttp
import xmltodict
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("PubMed Server", json_response=True)

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{PUBMED_BASE}/esearch.fcgi"
EFETCH_URL  = f"{PUBMED_BASE}/efetch.fcgi"


def get_ncbi_api_key() -> Optional[str]:
    # Optional key for higher rate limits
    return os.getenv("NCBI_API_KEY")


async def pubmed_esearch(
    session: aiohttp.ClientSession,
    term: str,
    sort: str = "pub_date",
    retmax: int = 5,
) -> list[str]:
    api_key = get_ncbi_api_key()

    try:
        retmax_int = int(retmax)
    except Exception:
        retmax_int = 5
    retmax_int = max(1, min(retmax_int, 100))

    params: dict[str, Any] = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": str(retmax_int),
        "sort": sort,
    }
    if api_key:
        params["api_key"] = api_key

    async with session.get(ESEARCH_URL, params=params) as resp:
        resp.raise_for_status()
        data = await resp.json()

    idlist = data.get("esearchresult", {}).get("idlist", []) or []
    return [str(pmid) for pmid in idlist]


def _normalize_pubmed_article(article: dict) -> dict:
    medline = article.get("MedlineCitation", {})
    art     = medline.get("Article", {})

    pmid = medline.get("PMID") or ""
    if isinstance(pmid, dict):
        pmid = pmid.get("#text", "") or ""

    title   = art.get("ArticleTitle", "No title available")
    journal = art.get("Journal", {}).get("Title", "Unknown journal")

    pubdate = art.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
    if isinstance(pubdate, dict):
        year = pubdate.get("Year")
        medline_date = pubdate.get("MedlineDate")
        date_str = year or medline_date or "Unknown date"
    else:
        date_str = "Unknown date"

    authors: list[str] = []
    author_list = art.get("AuthorList", {}).get("Author", [])
    if isinstance(author_list, dict):
        author_list = [author_list]

    for a in author_list:
        if not isinstance(a, dict):
            continue
        fore = a.get("ForeName", "") or a.get("Initials", "")
        last = a.get("LastName", "")
        if fore or last:
            authors.append(f"{fore} {last}".strip())

    abstract_text = ""
    abstract_obj = art.get("Abstract")
    if abstract_obj:
        secs = abstract_obj.get("AbstractText", "")
        if isinstance(secs, list):
            parts = []
            for sec in secs:
                if isinstance(sec, dict):
                    parts.append(sec.get("#text", "") or "")
                else:
                    parts.append(str(sec))
            abstract_text = " ".join(filter(None, parts))
        elif isinstance(secs, dict):
            abstract_text = secs.get("#text", "") or ""
        else:
            abstract_text = str(secs)

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "date": date_str,
        "authors": authors,
        "abstract": abstract_text.strip(),
        "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
    }


async def pubmed_efetch(
    session: aiohttp.ClientSession,
    pmids: list[str],
) -> list[dict]:
    api_key = get_ncbi_api_key()

    clean_pmids = [str(p).strip() for p in pmids if str(p).strip()]
    if not clean_pmids:
        return []

    params: dict[str, Any] = {
        "db": "pubmed",
        "id": ",".join(clean_pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    if api_key:
        params["api_key"] = api_key

    async with session.get(EFETCH_URL, params=params) as resp:
        resp.raise_for_status()
        text = await resp.text()

    doc = xmltodict.parse(text).get("PubmedArticleSet", {})
    articles = doc.get("PubmedArticle", []) or []
    if isinstance(articles, dict):
        articles = [articles]

    out: list[dict] = []
    for art in articles:
        if isinstance(art, dict):
            out.append(_normalize_pubmed_article(art))
    return out


@mcp.tool()
async def pubmed_handle_request(
    action: str,
    term: Optional[str] = None,
    pmid: Optional[str] = None,
    pmids: Optional[list[str]] = None,
    retmax: int = 5,
    sort: str = "pub_date",
) -> dict:
    """
    action: must be "search_fetch"
    term: PubMed query for ESearch (if PMIDs are not provided)
    pmid / pmids: explicit PMIDs to fetch (skips ESearch)
    retmax: 1..50 (server clamps)
    sort: "pub_date" or "relevance" (server normalizes)
    """

    action = (action or "").strip()
    if action != "search_fetch":
        return {"error": "unsupported_action", "action": action}

    try:
        retmax_i = int(retmax)
    except Exception:
        retmax_i = 5
    retmax_i = max(1, min(retmax_i, 50))

    sort_raw = (sort or "").strip().lower()
    if sort_raw in ("", "latest", "newest", "recent", "pub_date", "pub date"):
        sort_norm = "pub_date"
    elif sort_raw in ("relevance", "relevant", "best_match", "best match"):
        sort_norm = "relevance"
    else:
        sort_norm = "pub_date"

    used_pmids: list[str] = []
    if pmids and isinstance(pmids, list):
        used_pmids = [str(x).strip() for x in pmids if str(x).strip()]
    if (pmid or "").strip() and not used_pmids:
        used_pmids = [str(pmid).strip()]

    term_norm = (term or "").strip()

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if used_pmids:
            used_pmids = used_pmids[:retmax_i]
        elif term_norm:
            idlist = await pubmed_esearch(session, term_norm, sort=sort_norm, retmax=retmax_i)
            used_pmids = idlist[:retmax_i]
        else:
            return {"error": "missing_term_and_pmids", "tool_args": {"action": action}}

        articles = await pubmed_efetch(session, used_pmids)

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    return {
        "action": action,
        "term": term_norm if term_norm else None,
        "pmids": used_pmids,
        "retmax": retmax_i,
        "sort": sort_norm,
        "count": len(articles),
        "articles": articles,
        "timestamp_utc": timestamp,
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")  # http://localhost:8000/mcp
