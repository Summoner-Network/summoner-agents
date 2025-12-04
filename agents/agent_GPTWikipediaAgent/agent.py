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
from datetime import datetime, timezone
from urllib.parse import quote

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

# -------------------- Wikipedia helpers --------------------

WIKI_SEARCH_BASE  = "https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
WIKI_SUMMARY_BASE = "https://{lang}.wikipedia.org/api/rest_v1/page/summary"

WIKIPEDIA_DEFAULT_HEADERS = {
    # Feel free to customize this string for your own project/contact
    "User-Agent": "Summoner-GPTWikipediaAgent/0.1 (bot; contact: you@example.com)",
    "Accept": "application/json",
}


async def _wikipedia_search_titles(
    session: aiohttp.ClientSession,
    query: str,
    *,
    limit: int = 5,
    lang: str = "en",
) -> dict:
    """
    Call the Wikipedia REST search/title endpoint and return a normalized payload.
    """
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
    """
    Call the Wikipedia REST page/summary endpoint for a given title.
    """
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


async def wikipedia_handle_request(tool_args: dict) -> dict:
    """
    High-level helper used by GPTWikipediaAgent.

    Supported actions:
      - 'search_titles'     → search for page titles matching a query
      - 'summary'           → get summary for a specific title
      - 'search_summary'    → search then return summary for the top match
    """
    action = (tool_args.get("action") or "").strip()
    if not action:
        return {
            "error": "missing_action",
            "tool_args": tool_args,
        }

    # language (optional)
    lang_raw = (tool_args.get("lang") or "en").strip().lower()
    # keep it simple: only check it's non-empty, fall back to 'en' otherwise
    lang = lang_raw or "en"

    # Small helper to build timestamp
    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    async with aiohttp.ClientSession(headers=WIKIPEDIA_DEFAULT_HEADERS) as session:
        if action == "search_titles":
            query = (tool_args.get("query") or "").strip()
            if not query:
                return {
                    "error": "missing_query",
                    "tool_args": tool_args,
                }
            try:
                limit = int(tool_args.get("limit", 5))
            except Exception:
                limit = 5
            limit = max(1, min(limit, 50))

            core = await _wikipedia_search_titles(session, query, limit=limit, lang=lang)
            core["action"] = "search_titles"
            core["timestamp_utc"] = timestamp
            return core

        elif action == "summary":
            title = (tool_args.get("title") or "").strip()
            if not title:
                return {
                    "error": "missing_title",
                    "tool_args": tool_args,
                }

            core = await _wikipedia_summary(session, title, lang=lang)
            core["action"] = "summary"
            core["timestamp_utc"] = timestamp
            return core

        elif action == "search_summary":
            query = (tool_args.get("query") or "").strip()
            if not query:
                return {
                    "error": "missing_query",
                    "tool_args": tool_args,
                }
            try:
                limit = int(tool_args.get("limit", 5))
            except Exception:
                limit = 5
            limit = max(1, min(limit, 50))

            search_res = await _wikipedia_search_titles(
                session, query, limit=limit, lang=lang
            )
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
            summary_res = await _wikipedia_summary(
                session, top["title"], lang=lang
            )

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

        else:
            return {
                "error": "unsupported_action",
                "action": action,
                "tool_args": tool_args,
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
agent = MyAgent(name="GPTWikipediaAgent")

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

    # Ask GPT whether to call the Wikipedia tool and with what parameters
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing=agent.output_parsing,  # usually "json"
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

    action = ""
    if isinstance(tool_args, dict):
        action = (tool_args.get("action") or "").strip()

    if tool_args and action:
        api_result = await wikipedia_handle_request(tool_args)
        performed_call = True
    else:
        api_result = {
            "error": "no_wikipedia_call_requested_or_missing_action",
            "tool_args": tool_args,
        }

    # Build outgoing message
    output: dict[str, Any] = {
        "tool": "wikipedia",
        "performed_call": performed_call,
        "result": api_result,
        "tool_args": tool_args,
    }

    # Only add 'to' if content is a dict with 'from'
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
