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

import asyncpraw
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

# -------------------- Reddit helpers --------------------

def _get_reddit_credentials() -> tuple[Optional[dict], Optional[list[str]]]:
    """
    Load Reddit credentials from environment.

    Required:
      - REDDIT_CLIENT_ID
      - REDDIT_CLIENT_SECRET
      - REDDIT_USERNAME
      - REDDIT_PW

    Optional:
      - REDDIT_USER_AGENT
    """
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PW")
    user_agent = os.getenv("REDDIT_USER_AGENT")

    missing: list[str] = []
    if not client_id:
        missing.append("REDDIT_CLIENT_ID")
    if not client_secret:
        missing.append("REDDIT_CLIENT_SECRET")
    if not username:
        missing.append("REDDIT_USERNAME")
    if not password:
        missing.append("REDDIT_PW")

    if missing:
        return None, missing

    if not user_agent:
        # Fallback user agent
        user_agent = f"summoner-reddit-bot by u/{username}"

    creds = {
        "client_id": client_id,
        "client_secret": client_secret,
        "username": username,
        "password": password,
        "user_agent": user_agent,
    }
    return creds, None


def _serialize_submission(submission: Any) -> dict:
    """Convert a PRAW submission into a simple dict."""
    snippet = (submission.selftext or "").strip()
    if len(snippet) > 300:
        snippet = snippet[:300] + "…"

    return {
        "id": submission.id,
        "title": submission.title,
        "subreddit": str(submission.subreddit),
        "author": str(submission.author) if submission.author else None,
        "score": submission.score,
        "num_comments": submission.num_comments,
        "created_utc": submission.created_utc,
        "url": submission.url,
        "permalink": f"https://www.reddit.com{submission.permalink}",
        "selftext_snippet": snippet,
    }


def _serialize_comment(comment: Any) -> dict:
    """Convert a PRAW comment into a simple dict."""
    return {
        "id": comment.id,
        "author": str(comment.author) if comment.author else None,
        "score": comment.score,
        "created_utc": comment.created_utc,
        "body": comment.body,
        "permalink": f"https://www.reddit.com{comment.permalink}",
    }


async def _reddit_subreddit_posts(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'subreddit_posts'

    Expected tool_args keys:
      - subreddit (required)
      - sort (optional: 'hot'|'new'|'top'|'rising'|'controversial')
      - limit (optional: 1..50, default 10)
      - query (optional: if present, perform a subreddit search instead of raw listing)
    """
    raw_subreddit = (tool_args.get("subreddit") or "").strip()
    if raw_subreddit.lower().startswith("r/"):
        raw_subreddit = raw_subreddit[2:]
    subreddit_name = raw_subreddit

    if not subreddit_name:
        return {
            "error": "missing_subreddit",
            "tool_args": tool_args,
        }

    sort_raw = (tool_args.get("sort") or "hot").strip().lower()
    allowed_sorts = {"hot", "new", "top", "rising", "controversial"}
    sort = sort_raw if sort_raw in allowed_sorts else "hot"

    try:
        limit = int(tool_args.get("limit", 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    query = (tool_args.get("query") or "").strip() or None

    sub = await reddit.subreddit(subreddit_name)

    posts: list[dict] = []
    if query:
        # Subreddit search
        async for s in sub.search(
            query=query,
            sort="relevance",
            time_filter="all",
            limit=limit,
        ):
            posts.append(_serialize_submission(s))
    else:
        # Simple listing
        listing = getattr(sub, sort, sub.hot)
        async for s in listing(limit=limit):
            posts.append(_serialize_submission(s))

    return {
        "subreddit": subreddit_name,
        "sort": sort,
        "limit": limit,
        "query": query,
        "count": len(posts),
        "posts": posts,
    }


async def _reddit_search(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'search'

    Expected tool_args keys:
      - query (required)
      - subreddit (optional: 'all' or a specific subreddit)
      - sort (optional: 'relevance'|'hot'|'new'|'top'|'comments')
      - time_filter (optional: 'all'|'hour'|'day'|'week'|'month'|'year')
      - limit (optional: 1..50, default 10)
    """
    query = (tool_args.get("query") or "").strip()
    if not query:
        return {
            "error": "missing_query",
            "tool_args": tool_args,
        }

    raw_subreddit = (tool_args.get("subreddit") or "").strip()
    if raw_subreddit.lower().startswith("r/"):
        raw_subreddit = raw_subreddit[2:]
    subreddit_name = raw_subreddit or "all"

    sort_raw = (tool_args.get("sort") or "relevance").strip().lower()
    allowed_sorts = {"relevance", "hot", "new", "top", "comments"}
    sort = sort_raw if sort_raw in allowed_sorts else "relevance"

    time_raw = (tool_args.get("time_filter") or "").strip().lower()
    allowed_time = {"all", "hour", "day", "week", "month", "year"}
    time_filter = time_raw if time_raw in allowed_time else "month"

    try:
        limit = int(tool_args.get("limit", 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    sub = await reddit.subreddit(subreddit_name)

    posts: list[dict] = []
    async for s in sub.search(
        query=query,
        sort=sort,
        time_filter=time_filter,
        limit=limit,
    ):
        posts.append(_serialize_submission(s))

    return {
        "query": query,
        "subreddit": subreddit_name,
        "sort": sort,
        "time_filter": time_filter,
        "limit": limit,
        "count": len(posts),
        "posts": posts,
    }


async def _reddit_comments(
    reddit: asyncpraw.Reddit,
    tool_args: dict,
) -> dict:
    """
    action = 'comments'

    Expected tool_args keys:
      - submission_id (optional)
      - submission_url (optional)
      - sort (optional: 'top'|'new'|'controversial'|'old'|'qa')
      - limit (optional: number of comments after flattening, default 20)
    """
    submission_url = (tool_args.get("submission_url") or "").strip()
    submission_id = (tool_args.get("submission_id") or "").strip()

    if not submission_url and not submission_id:
        return {
            "error": "missing_submission_reference",
            "tool_args": tool_args,
        }

    sort_raw = (tool_args.get("sort") or "top").strip().lower()
    allowed_sorts = {"top", "new", "controversial", "old", "qa"}
    sort = sort_raw if sort_raw in allowed_sorts else "top"

    try:
        limit = int(tool_args.get("limit", 20))
    except Exception:
        limit = 20
    limit = max(1, min(limit, 100))

    if submission_url:
        submission = await reddit.submission(url=submission_url)
    else:
        submission = await reddit.submission(id=submission_id)

    submission.comment_sort = sort
    await submission.comments.replace_more(limit=0)
    all_comments = submission.comments.list()

    comments: list[dict] = []
    for c in all_comments[:limit]:
        comments.append(_serialize_comment(c))

    return {
        "submission_id": submission.id,
        "submission_url": f"https://www.reddit.com{submission.permalink}",
        "sort": sort,
        "limit": limit,
        "count": len(comments),
        "comments": comments,
    }


async def reddit_handle_request(tool_args: dict) -> dict:
    """
    High-level helper used by GPTRedditAgent.

    Supported actions:
      - 'subreddit_posts'
      - 'search'
      - 'comments'
    """
    action = (tool_args.get("action") or "").strip()
    if not action:
        return {
            "error": "missing_action",
            "tool_args": tool_args,
        }

    creds, missing = _get_reddit_credentials()
    if missing:
        return {
            "error": "missing_reddit_credentials",
            "details": f"Missing environment variables: {', '.join(missing)}",
            "tool_args": tool_args,
        }

    reddit = asyncpraw.Reddit(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        username=creds["username"],
        password=creds["password"],
        user_agent=creds["user_agent"],
    )

    try:
        if action == "subreddit_posts":
            core_result = await _reddit_subreddit_posts(reddit, tool_args)
        elif action == "search":
            core_result = await _reddit_search(reddit, tool_args)
        elif action == "comments":
            core_result = await _reddit_comments(reddit, tool_args)
        else:
            return {
                "error": "unsupported_action",
                "action": action,
                "tool_args": tool_args,
            }
    finally:
        await reddit.close()

    timestamp = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    result: dict[str, Any] = {
        "action": action,
        "timestamp_utc": timestamp,
    }
    result.update(core_result)
    return result

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
agent = MyAgent(name="GPTRedditAgent")

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

    # Ask GPT whether to call the Reddit tool and with what parameters
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
        api_result = await reddit_handle_request(tool_args)
        performed_call = True
    else:
        api_result = {
            "error": "no_reddit_call_requested_or_missing_action",
            "tool_args": tool_args,
        }

    # Build outgoing message
    output: dict[str, Any] = {
        "tool": "reddit",
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
