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

# -------------------- GitHub helpers --------------------

def build_github_headers() -> dict:
    """
    Build GitHub API headers using an optional GITHUB_TOKEN from the environment.
    The .env file is loaded in MyAgent.__init__, so by the time we call this,
    environment variables should be available.
    """
    token = os.getenv("GITHUB_TOKEN")
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


async def github_fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    """
    One-shot JSON fetch with GitHub headers.
    """
    async with session.get(url, headers=build_github_headers()) as resp:
        resp.raise_for_status()
        return await resp.json()


async def github_latest_commits_summary(
    owner: str,
    repo: str,
    max_commits: int = 5,
) -> dict:
    """
    High-level helper used by the agent.

    Given an owner and repo, returns a summary of the latest commits,
    including message, author, timestamp, stats, and changed files.

    This is what the agent will return as its 'result' when a GitHub
    call is requested.
    """
    owner = (owner or "").strip()
    repo = (repo or "").strip()
    if not owner or not repo:
        return {
            "owner": owner,
            "repo": repo,
            "commits": [],
            "error": "owner_or_repo_missing",
        }

    try:
        max_commits = int(max_commits)
    except Exception:
        max_commits = 5

    # Clamp max_commits to a safe range
    max_commits = max(1, min(max_commits, 20))

    commits_url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits"
        f"?per_page={max_commits}"
    )

    summaries: list[dict] = []
    async with aiohttp.ClientSession() as session:
        try:
            commits = await github_fetch_json(session, commits_url)
        except Exception as e:
            return {
                "owner": owner,
                "repo": repo,
                "max_commits": max_commits,
                "commits": [],
                "error": f"fetch_commits_failed: {type(e).__name__}: {e}",
            }

        # Normalize to a list (GitHub returns a list, but be defensive)
        if isinstance(commits, dict):
            commits_list = [commits]
        else:
            commits_list = list(commits or [])

        commits_list = commits_list[:max_commits]

        # For each commit, enrich with stats + file changes via detail endpoint
        for c in commits_list:
            sha = c.get("sha")
            if not sha:
                continue

            details_url = (
                f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
            )
            try:
                detail = await github_fetch_json(session, details_url)
            except Exception:
                # If detail fails, fall back to shallow info
                detail = c

            commit_info = detail.get("commit", {})
            author_info = commit_info.get("author", {}) or {}
            author_name = author_info.get("name") or author_info.get("email")
            date = author_info.get("date")
            message = commit_info.get("message") or ""
            subject = message.splitlines()[0] if message else ""

            files_field = detail.get("files") or []
            if isinstance(files_field, dict):
                files_field = [files_field]

            files_summary = [
                {
                    "filename": f.get("filename"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "changes": f.get("changes"),
                }
                for f in files_field
            ]

            summaries.append(
                {
                    "sha": sha,
                    "short_sha": sha[:7],
                    "author": author_name,
                    "date": date,
                    "subject": subject,
                    "message": message,
                    "html_url": detail.get("html_url"),
                    "stats": detail.get("stats", {}),
                    "files": files_summary,
                }
            )

    return {
        "owner": owner,
        "repo": repo,
        "max_commits": max_commits,
        "count": len(summaries),
        "commits": summaries,
        "timestamp_utc": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
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
agent = MyAgent(name="GPTGitHubAgent")

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

    # Ask GPT whether to call the GitHub tool and with what parameters
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing=agent.output_parsing,  # "json"
        output_type=None,
        cost_limit=agent.cost_limit_usd,
        debug=agent.debug,
    )

    tool_args = result.get("output")

    # Normalize output to a dict
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except Exception as e:
            tool_args = {"_raw": tool_args, "parse_error": str(e)[:200]}
    elif not isinstance(tool_args, dict):
        tool_args = {}

    performed_call = False
    api_result: Any = None

    # Decide whether to call the GitHub API
    owner = tool_args.get("owner") if isinstance(tool_args, dict) else None
    repo = tool_args.get("repo") if isinstance(tool_args, dict) else None

    if (
        isinstance(tool_args, dict)
        and isinstance(owner, str)
        and isinstance(repo, str)
        and owner.strip()
        and repo.strip()
    ):
        max_commits = tool_args.get("max_commits", 5)
        api_result = await github_latest_commits_summary(
            owner=owner,
            repo=repo,
            max_commits=max_commits,
        )
        performed_call = True
    else:
        api_result = {
            "error": "no_github_call_requested_or_missing_owner_repo",
            "tool_args": tool_args,
        }

    # Build outgoing message
    output: dict[str, Any] = {
        "tool": "github",
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
