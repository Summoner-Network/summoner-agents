import warnings
warnings.filterwarnings("ignore", message=r".*supports OpenSSL.*LibreSSL.*")

from summoner.client import SummonerClient
from summoner.protocol import Direction
from typing import Any, Union, Optional, Type, Literal
from pathlib import Path
import argparse, json, asyncio, os

from aioconsole import aprint
from dotenv import load_dotenv
import openai
from openai import AsyncOpenAI

from safeguards import (
    count_chat_tokens,
    estimate_chat_request_cost,
    actual_chat_request_cost,
    get_usage_from_response,
)

import aiohttp
import xmltodict
from datetime import datetime, timezone

# -------------------- early parse so class can load configs --------------------
prompt_parser = argparse.ArgumentParser(add_help=False)
prompt_parser.add_argument("--gpt", dest="gpt_config_path", required=False, help="Path to gpt_config.json (defaults to file next to this script).")
prompt_parser.add_argument("--id", dest="id_json_path", required=False, help="Path to id.json (defaults to file next to this script).")
prompt_args, _ = prompt_parser.parse_known_args()

# -------------------- async queue --------------------
message_buffer: Optional[asyncio.Queue] = None

async def setup():
    """Initialize the internal message buffer used between receive/send handlers."""
    global message_buffer
    message_buffer = asyncio.Queue()

# -------------------- PubMed helpers --------------------

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{PUBMED_BASE}/esearch.fcgi"
EFETCH_URL  = f"{PUBMED_BASE}/efetch.fcgi"

def get_ncbi_api_key() -> Optional[str]:
    """
    Optional PubMed / NCBI API key.

    If present in NCBI_API_KEY, it will be added to E-utilities
    requests to increase rate limits.
    """
    return os.getenv("NCBI_API_KEY")


async def pubmed_esearch(
    session: aiohttp.ClientSession,
    term: str,
    sort: str = "pub_date",
    retmax: int = 5,
) -> list[str]:
    """
    ESearch wrapper for PubMed.

    sort:
      - 'pub_date'   → newest first
      - 'relevance'  → best match / relevance

    retmax: number of IDs to retrieve (1..100).
    """
    api_key = get_ncbi_api_key()

    # Bound retmax to a reasonable range
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
        "sort": sort,  # valid: 'pub_date', 'relevance', etc. (see NCBI docs)
    }
    if api_key:
        params["api_key"] = api_key

    async with session.get(ESEARCH_URL, params=params) as resp:
        resp.raise_for_status()
        data = await resp.json()

    idlist = data.get("esearchresult", {}).get("idlist", []) or []
    return [str(pmid) for pmid in idlist]


def _normalize_pubmed_article(article: dict) -> dict:
    """
    Normalize one PubMedArticle (from xmltodict) into a simple dict.

    Returns:
      {
        "pmid": str,
        "title": str,
        "journal": str,
        "date": str,
        "authors": [str],
        "abstract": str,
        "link": str,
      }
    """
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
    """
    EFetch wrapper for PubMed.

    Accepts a list of PMIDs and returns a list of normalized article dicts.
    """
    api_key = get_ncbi_api_key()

    # Deduplicate / clean
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

    normalized: list[dict] = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        normalized.append(_normalize_pubmed_article(art))

    return normalized


async def pubmed_handle_request(tool_args: dict) -> dict:
    """
    High-level helper used by GPTPubMedAgent.

    Expected tool_args shape (GPT decides):

    {
      "action": "search_fetch",
      "term": "<search query>",          # optional if pmids provided
      "retmax": 5,                      # 1..50
      "sort": "pub_date" | "relevance", # optional (default 'pub_date')
      "pmids": ["12345678", "34567890"] # optional, overrides term if present
    }

    Behavior:

    - If 'pmids' is provided and non-empty, skip ESearch and just EFetch those IDs.
    - Else, if 'term' is provided, run ESearch with sort/retmax, then EFetch.
    - Else, return an error.

    Returns a dict with:
      {
        "action": "search_fetch",
        "term": ...,
        "pmids": [...],
        "retmax": N,
        "sort": "pub_date" | "relevance",
        "count": <number of articles>,
        "articles": [ {pmid, title, ...}, ... ],
        "timestamp_utc": "...",
      }
      or an error payload.
    """
    action = (tool_args.get("action") or "").strip()
    if action != "search_fetch":
        return {
            "error": "unsupported_action",
            "action": action,
            "tool_args": tool_args,
        }

    # retmax
    try:
        retmax = int(tool_args.get("retmax", 5))
    except Exception:
        retmax = 5
    retmax = max(1, min(retmax, 50))

    # sort: normalize some human-ish values
    sort_raw = (tool_args.get("sort") or "").strip().lower()
    if sort_raw in ("", "latest", "newest", "recent", "pub_date", "pub date"):
        sort = "pub_date"
    elif sort_raw in ("relevance", "relevant", "best_match", "best match"):
        sort = "relevance"
    else:
        # safe default
        sort = "pub_date"

    # pmids (if any)
    pmids_param = tool_args.get("pmids")
    pmids: list[str] = []
    if isinstance(pmids_param, str):
        # Allow comma or space separated
        if "," in pmids_param:
            pmids = [p.strip() for p in pmids_param.split(",")]
        else:
            pmids = [p.strip() for p in pmids_param.split()]
    elif isinstance(pmids_param, list):
        pmids = [str(p).strip() for p in pmids_param if str(p).strip()]

    # Single 'pmid' convenience
    single_pmid = tool_args.get("pmid")
    if single_pmid and not pmids:
        pmids = [str(single_pmid).strip()]

    term = (tool_args.get("term") or "").strip()

    # If we have PMIDs, we just fetch them (optionally respecting retmax)
    async with aiohttp.ClientSession() as session:
        used_pmids: list[str] = []

        if pmids:
            used_pmids = pmids[:retmax]
        elif term:
            # ESearch to get IDs for the term
            idlist = await pubmed_esearch(
                session=session,
                term=term,
                sort=sort,
                retmax=retmax,
            )
            if not idlist:
                timestamp = (
                    datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
                return {
                    "action": action,
                    "term": term,
                    "pmids": [],
                    "retmax": retmax,
                    "sort": sort,
                    "count": 0,
                    "articles": [],
                    "timestamp_utc": timestamp,
                }
            used_pmids = idlist[:retmax]
        else:
            return {
                "error": "missing_term_and_pmids",
                "tool_args": tool_args,
            }

        # Fetch article details
        articles = await pubmed_efetch(session=session, pmids=used_pmids)

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    return {
        "action": action,
        "term": term if term else None,
        "pmids": used_pmids,
        "retmax": retmax,
        "sort": sort,
        "count": len(articles),
        "articles": articles,
        "timestamp_utc": timestamp,
    }

# -------------------- agent --------------------
class MyAgent(SummonerClient):
    def __init__(self, name: Optional[str] = None):
        super().__init__(name=name)

        # base dir
        try:
            self.base_dir = Path(__file__).resolve().parent
        except NameError:
            self.base_dir = Path.cwd()

        # env / client
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY missing in environment.")
        self.client = AsyncOpenAI(api_key=api_key)

        # ----- GPT config -----
        gpt_cfg_path = Path(prompt_args.gpt_config_path) if prompt_args.gpt_config_path else (self.base_dir / "gpt_config.json")
        self.gpt_cfg = self._load_json(gpt_cfg_path)

        # chat knobs (all configurable)
        self.model                  = self.gpt_cfg.get("model", "gpt-4o-mini")
        self.sleep_seconds          = float(self.gpt_cfg.get("sleep_seconds", 0.5))
        self.output_parsing         = self.gpt_cfg.get("output_parsing", "json")  # "text"|"json"|"structured"
        self.cost_limit_usd         = self.gpt_cfg.get("cost_limit_usd")          # None or float
        self.debug                  = bool(self.gpt_cfg.get("debug", False))
        self.max_chat_input_tokens  = int(self.gpt_cfg.get("max_chat_input_tokens", 4000))
        self.max_chat_output_tokens = int(self.gpt_cfg.get("max_chat_output_tokens", 1500))

        # prompts
        self.personality_prompt = (self.gpt_cfg.get("personality_prompt") or "").strip()
        self.format_prompt      = (self.gpt_cfg.get("format_prompt") or "").strip()
        if not self.format_prompt:
            self.logger.warning("[config] empty format_prompt")

        # identity (from --id or default id.json)
        id_path = Path(prompt_args.id_json_path) if prompt_args.id_json_path else (self.base_dir / "id.json")
        try:
            with id_path.open("r", encoding="utf-8") as f:
                id_dict: dict = json.load(f)
            self.my_id = str(id_dict.get("uuid") or "unknown")
        except Exception:
            self.my_id = "unknown"
            self.logger.warning("id.json missing or invalid; using my_id='unknown'")

        # optional: model id sanity check (best-effort)
        try:
            model_ids = [m.id for m in openai.models.list().data]
        except Exception:
            model_ids = []
        if model_ids and self.model not in model_ids:
            raise ValueError(f"Invalid model in gpt_config.json: {self.model}. "
                             f"Available: {', '.join(model_ids)}")

    # ------------- in-class helpers -------------

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _compose_user_prompt(self, payload: Any) -> str:
        """Join personality + the output format + the incoming message (JSON) into one user message."""
        personality = self.personality_prompt
        body = json.dumps(payload, ensure_ascii=False)
        return f"{personality}\n{self.format_prompt}\n\nContent:\n{body}\n"


    # -------------------- in-class safeguarded GPT call --------------------
    async def gpt_call_async(
        self,
        message: str,
        model_name: Optional[str] = None,
        output_parsing: Literal["text", "json", "structured"] = "json",
        output_type: Optional[Type] = None,
        cost_limit: Optional[float] = None,
        debug: Optional[bool] = None,
    ) -> dict[str, Any]:
        """
        Single-turn call with configurable token/cost guards.
        Returns {"output": <parsed or None>, "cost": <usd or None>}
        """
        model_name = model_name or self.model
        debug = self.debug if debug is None else debug
        cost_limit = self.cost_limit_usd if cost_limit is None else cost_limit

        messages: list[dict[str, str]] = [{"role": "user", "content": message}]

        prompt_tokens = count_chat_tokens(messages, model_name)
        if debug:
            await aprint(f"\033[96mPrompt tokens: {prompt_tokens} > {self.max_chat_input_tokens} ? {prompt_tokens > self.max_chat_input_tokens}\033[0m")
            messages_str = str(messages)
            long_messages = len(messages_str) > 2000
            await aprint(f"\033[92mInput: {messages_str[:2000] + '...' * long_messages}\033[0m")

        est_cost = estimate_chat_request_cost(model_name, prompt_tokens, self.max_chat_output_tokens)
        if debug:
            await aprint(f"\033[95m[chat] Estimated cost (for {self.max_chat_output_tokens} output tokens): ${est_cost:.6f}\033[0m")

        output: Any = None
        act_cost: Optional[float] = None

        # Guard 1: token ceiling
        if prompt_tokens >= self.max_chat_input_tokens:
            if debug:
                await aprint("\033[93mTokens exceeded — unable to send the request.\033[0m")
            return {"output": output, "cost": act_cost}

        # Guard 2: cost ceiling
        if cost_limit is not None and est_cost > cost_limit:
            if debug:
                await aprint(f"\033[93m[chat] Skipping request: estimated cost ${est_cost:.6f} exceeds cost_limit ${cost_limit:.6f}.\033[0m")
            return {"output": output, "cost": act_cost}

        # Proceed with the call
        if output_parsing == "text":
            response = await self.client.chat.completions.create(
                messages=messages,
                model=model_name,
                max_completion_tokens=self.max_chat_output_tokens,
            )
            usage = get_usage_from_response(response)
            if usage:
                act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
                if debug:
                    await aprint(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
            else:
                if debug:
                    await aprint("\033[93m[chat] Note: usage not available. Skipping cost.\033[0m")
            output = response.choices[0].message.content

        elif output_parsing == "json":
            response = await self.client.chat.completions.create(
                messages=messages,
                model=model_name,
                max_completion_tokens=self.max_chat_output_tokens,
                response_format={"type": "json_object"},
            )
            usage = get_usage_from_response(response)
            if usage:
                act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
                if debug:
                    await aprint(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
            else:
                if debug:
                    await aprint("\033[93m[chat] Note: usage not available. Skipping cost.\033[0m")
            try:
                output = json.loads(response.choices[0].message.content)
            except Exception:
                output = {}

        elif output_parsing == "structured":
            if output_type is None:
                raise ValueError("output_type (schema) is required when output_parsing='structured'.")
            response = await self.client.responses.parse(
                input=messages,
                model=model_name,
                max_output_tokens=self.max_chat_output_tokens,
                text_format=output_type,
            )
            usage = get_usage_from_response(response)
            if usage:
                act_cost = actual_chat_request_cost(model_name, usage.prompt_tokens, usage.completion_tokens)
                if debug:
                    await aprint(f"\033[95m[chat] Actual cost: ${act_cost:.6f}\033[0m")
            else:
                if debug:
                    await aprint("\033[93m[chat] Note: usage not available for structured response. Skipping cost.\033[0m")
            output = response.output[0].content[0].parsed

        else:
            raise ValueError(f"Unrecognized output_parsing: {output_parsing!r}")

        return {"output": output, "cost": act_cost}



# instantiate
agent = MyAgent(name="GPTPubMedAgent")

# -------------------- hooks --------------------
@agent.hook(direction=Direction.RECEIVE)
async def validate(msg: Any) -> Optional[dict]:
    if isinstance(msg, str) and msg.startswith("Warning:"):
        agent.logger.warning(msg.replace("Warning:", "[From Server]"))
        return  # drop

    if not (isinstance(msg, dict) and "remote_addr" in msg and "content" in msg):
        agent.logger.info("[hook:recv] missing address/content")
        return

    agent.logger.info(f"[hook:recv] {msg['remote_addr']} passed validation")
    return msg

@agent.hook(direction=Direction.SEND)
async def sign(msg: Any) -> Optional[dict]:
    agent.logger.info(f"[hook:send] sign {agent.my_id}")
    if isinstance(msg, str):
        msg = {"message": msg}
    if not isinstance(msg, dict):
        return
    msg.update({"from": agent.my_id})
    return msg

# -------------------- handlers --------------------
@agent.receive(route="")
async def receiver_handler(msg: Any) -> None:
    address = msg["remote_addr"]
    if msg["content"] in [{}, None]:
            return
    await message_buffer.put(msg["content"])
    agent.logger.info(f"Buffered message from:(SocketAddress={address}).")

@agent.send(route="")
async def send_handler() -> Union[dict, str]:
    content = await message_buffer.get()

    # Compose user prompt directly from config's prompts
    user_prompt = agent._compose_user_prompt(content)

    # Ask GPT whether to call the PubMed tool and with what parameters
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing=agent.output_parsing,  # "json"
        output_type=None,
        cost_limit=agent.cost_limit_usd,
        debug=agent.debug,
    )

    tool_args = result.get("output")

    # Normalize GPT output to a dict
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception as e:
            tool_args = {"_raw": tool_args, "parse_error": str(e)[:200]}
    elif not isinstance(tool_args, dict):
        tool_args = {}

    performed_call = False
    api_result: Any = None

    action = (tool_args.get("action") or "").strip() if isinstance(tool_args, dict) else ""

    if tool_args and action:
        api_result = await pubmed_handle_request(tool_args)
        performed_call = True
    else:
        api_result = {
            "error": "no_pubmed_call_requested_or_missing_action",
            "tool_args": tool_args,
        }

    # Build outgoing message
    output: dict[str, Any] = {
        "tool": "pubmed",
        "performed_call": performed_call,
        "result": api_result,
        "tool_args": tool_args,
    }

    if isinstance(content, dict) and "from" in content:
        output["to"] = content["from"]

    agent.logger.info(
        f"[respond] model={agent.model} id={agent.my_id} "
        f"cost={result.get('cost')} performed_call={performed_call}"
    )
    await asyncio.sleep(agent.sleep_seconds)

    return output


# -------------------- main --------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the client config (JSON), e.g., --config configs/client_config.json')
    args, _ = parser.parse_known_args()

    agent.loop.run_until_complete(setup())
    agent.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
