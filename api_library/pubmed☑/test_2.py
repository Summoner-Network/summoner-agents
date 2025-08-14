import asyncio
import aiohttp
from dotenv import load_dotenv
import os
import xmltodict

load_dotenv()
API_KEY = os.getenv("NCBI_API_KEY")  # optional

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH = f"{BASE}/esearch.fcgi"
EFETCH = f"{BASE}/efetch.fcgi"

async def esearch(session, term, retmax=1):
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

    doc = xmltodict.parse(text)["PubmedArticleSet"]["PubmedArticle"]

    # Normalize to list
    if not isinstance(doc, list):
        doc = [doc]

    article_data = doc[0].get("MedlineCitation", {}).get("Article", {})

    title = article_data.get("ArticleTitle", "No title available")
    journal = article_data.get("Journal", {}).get("Title", "Unknown journal")
    pubdate = article_data.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
    date = pubdate.get("Year") or pubdate.get("MedlineDate") or "Unknown date"

    authors = []
    for a in article_data.get("AuthorList", {}).get("Author", []):
        if not isinstance(a, dict):
            continue
        fore = a.get("ForeName", "")
        last = a.get("LastName", "")
        if fore or last:
            authors.append(f"{fore} {last}".strip())

    # Handle abstract sections
    abstract = ""
    if article_data.get("Abstract"):
        secs = article_data["Abstract"].get("AbstractText", "")
        if isinstance(secs, list):
            # Extract text from each section, even if stored as dict
            abstract_parts = []
            for sec in secs:
                if isinstance(sec, dict):
                    abstract_parts.append(sec.get("#text", ""))
                else:
                    abstract_parts.append(str(sec))
            abstract = " ".join(filter(None, abstract_parts))
        elif isinstance(secs, dict):
            abstract = secs.get("#text", "")
        else:
            abstract = str(secs)

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "date": date,
        "authors": authors,
        "abstract": abstract.strip(),
        "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    }


def print_paper_info(info):
    print("=" * 80)
    print(f"{info['title']}\n")
    print(f"Journal: {info['journal']}")
    print(f"Date: {info['date']}")
    print(f"Authors: {', '.join(info['authors'])}")
    print(f"PMID: {info['pmid']}")
    print(f"Link: {info['link']}\n")
    print("Abstract:")
    print(info["abstract"] or "(No abstract available)")
    print("=" * 80)
    print()

async def latest_papers(keywords):
    async with aiohttp.ClientSession() as session:
        for kw in keywords:
            print(f"Searching latest paper for: {kw}")
            pmids = await esearch(session, kw, retmax=1)
            if not pmids:
                print("  No results found.\n")
                continue
            info = await efetch(session, pmids[0])
            print_paper_info(info)

if __name__ == "__main__":
    keywords = ["machine learning cancer", "CRISPR therapy"]  # example
    asyncio.run(latest_papers(keywords))
