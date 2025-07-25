import os
import sys
import asyncio
import aiohttp
import xmltodict
from pprint import pprint
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("NCBI_API_KEY")  # optional

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH = f"{BASE}/esearch.fcgi"
EFETCH  = f"{BASE}/efetch.fcgi"

async def esearch(session, term, retmax=20):
    params = {
        "db": "pubmed",
        "term": term,
        "sort": "pub+date",
        "retmode": "json",
        "retmax": retmax,
        **({"api_key": API_KEY} if API_KEY else {})
    }
    async with session.get(ESEARCH, params=params) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return data["esearchresult"]["idlist"]

async def efetch(session, pmid):
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
        **({"api_key": API_KEY} if API_KEY else {})
    }
    async with session.get(EFETCH, params=params) as resp:
        resp.raise_for_status()
        text = await resp.text()
    # Parse the minimal fields we want
    doc = xmltodict.parse(text)["PubmedArticleSet"]["PubmedArticle"][0]
    article = doc["MedlineCitation"]["Article"]
    title = article["ArticleTitle"]
    journal = article["Journal"]["Title"]
    pubdate = article["Journal"]["JournalIssue"]["PubDate"]
    date = (
        pubdate.get("Year") or 
        pubdate.get("MedlineDate") or 
        ""
    )
    authors = []
    for a in article.get("AuthorList", {}).get("Author", []):
        last = a.get("LastName")
        fore = a.get("ForeName")
        if last and fore:
            authors.append(f"{fore} {last}")
    abstract = ""
    if article.get("Abstract"):
        secs = article["Abstract"].get("AbstractText", "")
        if isinstance(secs, list):
            abstract = " ".join(secs)
        else:
            abstract = secs
    link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "date": date,
        "authors": authors,
        "abstract_snippet": abstract[:300] + ("…" if len(abstract)>300 else ""),
        "link": link
    }

async def monitor_pubmed(term, interval=60):
    seen = None
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                pmids = await esearch(session, term, retmax=20)
                if not pmids:
                    print("No results for term:", term)
                else:
                    # on first run, set baseline
                    if seen is None:
                        seen = pmids[0]
                        print(f"[{datetime.utcnow().isoformat()}Z] Monitoring term '{term}'; latest PMID={seen}")
                    else:
                        # find new PMIDs before seen
                        new = []
                        for pid in pmids:
                            if pid == seen:
                                break
                            new.append(pid)
                        if new:
                            for pid in reversed(new):
                                info = await efetch(session, pid)
                                # one-line summary
                                print(f"[{info['date']}] PMID {pid}: {info['title']}")
                                # full metadata
                                pprint(info)
                                print()
                            seen = pmids[0]
                await asyncio.sleep(interval)
            except Exception as e:
                print("Error:", e)
                await asyncio.sleep(interval)


async def watch_term(session, term, interval, seen_map):
    """Poll PubMed for a single term."""
    seen = seen_map.setdefault(term, None)
    pmids = await esearch(session, term)
    if not pmids:
        print(f"[{datetime.utcnow().isoformat()}Z] No results for '{term}'")
        return

    # First run: set baseline
    if seen is None:
        seen_map[term] = pmids[0]
        print(f"[{datetime.utcnow().isoformat()}Z] Tracking '{term}'; latest PMID={pmids[0]}")
        return

    # Identify any new PMIDs
    new = []
    for pid in pmids:
        if pid == seen:
            break
        new.append(pid)

    # Print new articles (oldest first)
    for pid in reversed(new):
        info = await efetch(session, pid)
        # one-line header
        print(f"[{info['date']}] PMID {pid}: {info['title']}")
        # full metadata
        pprint(info)
        print()

    if new:
        seen_map[term] = pmids[0]

async def monitor_terms(terms, interval=60):
    seen_map = {}  # term → last_seen_pmid
    async with aiohttp.ClientSession() as session:
        print(f"Monitoring {len(terms)} term(s) every {interval}s:\n  " + ", ".join(terms))
        while True:
            tasks = [watch_term(session, t, interval, seen_map) for t in terms]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)

if __name__=="__main__":
    # if len(sys.argv)<2 or len(sys.argv)>3:
    #     print("Usage: python pubmed_monitor.py <search_term> [--interval N]")
    #     sys.exit(1)
    # term = sys.argv[1]
    # interval = int(sys.argv[2]) if len(sys.argv)==3 else 60
    # print(f"Monitoring PubMed for '{term}' every {interval}s…\n")
    # asyncio.run(monitor_pubmed(term, interval))

    if len(sys.argv) < 2:
        print("Usage: python pubmed_monitor.py <term1> [<term2> ...] [interval_seconds]")
        sys.exit(1)

    # Last arg numeric? treat as interval
    *terms, last = sys.argv[1:]
    if last.isdigit():
        interval = int(last)
        terms = terms
    else:
        interval = 60

    print(f"Starting PubMed monitor for terms: {terms} (interval={interval}s)\n")
    asyncio.run(monitor_terms(terms, interval))
