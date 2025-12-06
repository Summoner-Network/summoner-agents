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

# Slack SDK
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.errors import SlackApiError  # <-- add this

# -------------------------------------------------------------------------
#  Env + allowed channels
# -------------------------------------------------------------------------
# Load .env once at module import so ALLOWED_CHANNEL_IDS can see it.
load_dotenv()

# Static allowlist for posting
ALLOWED_CHANNEL_IDS = {
    # by ID:
    "C07AA1K6BQB": "#general",
    "C08K6V1SVB2": "#gm-ga-ge-gn",
    # optionally by name for convenience:
    "#general": "C07AA1K6BQB",
    "#gm-ga-ge-gn": "C08K6V1SVB2",
}

# Runtime blocklist learned from Slack errors
BLOCKED_CHANNEL_IDS: set[str] = set()
BLOCKED_CHANNEL_NAMES: set[str] = set()


def normalize_post_channel(raw: str) -> Optional[str]:
    """
    Turn a user/GPT channel token into a Slack channel ID we may post to.
    Returns None if the channel is not allowed.
    """
    if not raw:
        return None

    # If GPT gave us something like "#general"
    if raw in ALLOWED_CHANNEL_IDS:
        cid = ALLOWED_CHANNEL_IDS[raw]
        return cid if cid not in BLOCKED_CHANNEL_IDS else None

    # If GPT gave an ID directly
    if raw in ALLOWED_CHANNEL_IDS.values():
        return raw if raw not in BLOCKED_CHANNEL_IDS else None

    # Anything else is not allowed
    return None

# New: channels where posting failed (e.g. not_in_channel)
NOT_ALLOWED_CHANNEL_IDS: set[str] = set()

# -------------------- early parse so class can load configs --------------------
prompt_parser = argparse.ArgumentParser(add_help=False)
prompt_parser.add_argument("--gpt", dest="gpt_config_path", required=False, help="Path to gpt_config.json (defaults to file next to this script).")
prompt_parser.add_argument("--id", dest="id_json_path", required=False, help="Path to id.json (defaults to file next to this script).")
prompt_args, _ = prompt_parser.parse_known_args()

# -------------------- async queues --------------------
# From Slack to Summoner (relay user requests)
to_server_buffer: Optional[asyncio.Queue] = None

# From Summoner to Slack (post replies)
from_server_buffer: Optional[asyncio.Queue] = None

# Optional: use a separate queue for already GPT-shaped Slack posts
to_slack_buffer: Optional[asyncio.Queue] = None

# Slack clients and readiness flag
SLACK_WEB_CLIENT: Optional[AsyncWebClient] = None
SLACK_SOCKET_CLIENT: Optional[SocketModeClient] = None
slack_ready: Optional[asyncio.Event] = None

# Simple dedupe for Slack events: (channel, ts) -> seen
SEEN_SLACK_EVENTS: Optional[set[tuple[str, str]]] = None

async def setup() -> None:
    global to_server_buffer, from_server_buffer, to_slack_buffer, slack_ready, SEEN_SLACK_EVENTS
    to_server_buffer = asyncio.Queue()
    from_server_buffer = asyncio.Queue()
    to_slack_buffer = asyncio.Queue()
    slack_ready = asyncio.Event()
    SEEN_SLACK_EVENTS = set()

# -------------------- Slack helpers --------------------

async def slack_handle_events(sm_client: SocketModeClient, req: SocketModeRequest):
    """
    Main handler for everything Slack sends over Socket Mode.

    We keep it as thin as possible: normalize events and push them
    into to_server_buffer. GPT decisions happen in @send('relay').
    """
    global to_server_buffer, SEEN_SLACK_EVENTS

    # 1) ACK the envelope so Slack does not retry
    await sm_client.send_socket_mode_response(
        SocketModeResponse(envelope_id=req.envelope_id)
    )

    if req.type != "events_api":
        return

    event = req.payload.get("event", {}) or {}
    event_type = event.get("type")

    if event_type in {"app_mention", "message"} and not event.get("bot_id"):
        text    = (event.get("text") or "").strip()
        channel = event.get("channel")
        user    = event.get("user")
        ts      = event.get("ts")

        if not (channel and user and text and ts):
            return

        # 1) Hard block: channels that we know are not usable
        if channel in NOT_ALLOWED_CHANNEL_IDS:
            agent.logger.info(f"[slack] ignoring event from NOT_ALLOWED channel={channel}")
            return

        # 2) Soft allowlist: if ALLOWED_CHANNEL_IDS is non-empty, we only listen to those
        if ALLOWED_CHANNEL_IDS and channel not in ALLOWED_CHANNEL_IDS:
            agent.logger.info(f"[slack] ignoring event from disallowed channel={channel}")
            return

        # 3) Dedupe (same channel+ts can come as message + app_mention)
        if SEEN_SLACK_EVENTS is not None:
            key = (channel, ts)
            if key in SEEN_SLACK_EVENTS:
                agent.logger.info(f"[slack] duplicate event channel={channel} ts={ts}; ignoring")
                return
            SEEN_SLACK_EVENTS.add(key)

        payload = {
            "source": "slack",
            "event_type": event_type,
            "channel": channel,
            "user": user,
            "ts": ts,
            "text": text,
        }

        if to_server_buffer is not None:
            await to_server_buffer.put(payload)
        agent.logger.info(f"[slack] buffered event from user={user} channel={channel}")


async def slack_socket_loop() -> None:
    """Connect to Slack Socket Mode and keep listening for events."""
    global SLACK_WEB_CLIENT, SLACK_SOCKET_CLIENT, slack_ready

    load_dotenv()
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    app_token = os.getenv("SLACK_APP_TOKEN")

    if not bot_token or not app_token:
        agent.logger.warning("SLACK_BOT_TOKEN or SLACK_APP_TOKEN missing; Slack integration disabled.")
        return

    SLACK_WEB_CLIENT = AsyncWebClient(token=bot_token)
    SLACK_SOCKET_CLIENT = SocketModeClient(
        app_token=app_token,
        web_client=SLACK_WEB_CLIENT,
    )

    SLACK_SOCKET_CLIENT.socket_mode_request_listeners.append(slack_handle_events)

    try:
        await SLACK_SOCKET_CLIENT.connect()
        agent.logger.info("Slack Socket Mode client connected.")
        if slack_ready is not None:
            slack_ready.set()

        # Keep task alive until cancelled
        await asyncio.Event().wait()

    except asyncio.CancelledError:
        agent.logger.info("Slack socket loop cancelled.")
        # fall through to finally for cleanup
        raise

    finally:
        # Best-effort cleanup of Slack clients
        try:
            if SLACK_SOCKET_CLIENT is not None:
                await SLACK_SOCKET_CLIENT.close()
        except Exception as e:
            agent.logger.warning(
                f"Error closing Slack SocketModeClient: {type(e).__name__}: {e}"
            )

        # AsyncWebClient in slack_sdk does not expose an async .close();
        # resources are managed by SocketModeClient. We just drop references.
        SLACK_SOCKET_CLIENT = None
        SLACK_WEB_CLIENT = None

        agent.logger.info("Slack clients closed.")


async def slack_post_loop() -> None:
    """Read shaped Slack messages from to_slack_buffer and post them."""
    global to_slack_buffer, SLACK_WEB_CLIENT, slack_ready

    if slack_ready is None or to_slack_buffer is None:
        return

    await slack_ready.wait()

    if SLACK_WEB_CLIENT is None:
        agent.logger.warning("[slack_post_loop] SLACK_WEB_CLIENT is None")
        return

    while True:
        payload = await to_slack_buffer.get()
        raw_channel = payload.get("channel")
        text       = payload.get("text")
        thread_ts  = payload.get("thread_ts")

        if not raw_channel or not text:
            continue

        channel = normalize_post_channel(str(raw_channel))
        if channel is None:
            agent.logger.info(
                f"[slack_post_loop] suppressed post to disallowed/unknown channel={raw_channel!r}"
            )
            continue

        try:
            kwargs = {"channel": channel, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            await SLACK_WEB_CLIENT.chat_postMessage(**kwargs)
            agent.logger.info(f"[slack_post_loop] posted to channel={channel}")
        except Exception as e:
            from slack_sdk.errors import SlackApiError

            agent.logger.warning(
                f"[slack_post_loop] failed to post: {type(e).__name__}: {e}"
            )

            # Learn from Slack's error
            if isinstance(e, SlackApiError):
                err = (e.response.get("error") or "").strip()
                if err == "not_in_channel":
                    # Mark that channel as blocked so we never try again
                    BLOCKED_CHANNEL_IDS.add(channel)
                    # Also keep its human name, if we have one
                    for key, val in ALLOWED_CHANNEL_IDS.items():
                        if val == channel and key.startswith("#"):
                            BLOCKED_CHANNEL_NAMES.add(key)

                    agent.logger.info(
                        f"[slack_post_loop] learned not_in_channel for channel_id={channel}; "
                        f"future posts to this channel will be suppressed."
                    )

def build_channel_policy_clause() -> str:
    """
    Describe allowed and blocked channels to the model in natural language.
    """
    allowed_names: set[str] = set()
    for k, v in ALLOWED_CHANNEL_IDS.items():
        # k can be "#general"; v is "C07..."
        if k.startswith("#"):
            allowed_names.add(k)

    parts: list[str] = []
    if allowed_names:
        allowed_str = ", ".join(sorted(allowed_names))
        parts.append(
            "You are only allowed to post into the following Slack channels: "
            f"{allowed_str}. If the Content asks to post in any other channel "
            "(for example \"funding\" or \"#funding\"), you must output "
            "{\"should_post\": false}."
        )

    if BLOCKED_CHANNEL_NAMES or BLOCKED_CHANNEL_IDS:
        blocked_tokens = set(BLOCKED_CHANNEL_NAMES)
        blocked_tokens.update(BLOCKED_CHANNEL_IDS)
        blocked_str = ", ".join(sorted(blocked_tokens))
        parts.append(
            "The following channels are currently unavailable (the bot is not in them): "
            f"{blocked_str}. You must not attempt to post to them and should return "
            "{\"should_post\": false} if Content targets one of these."
        )

    return "\n".join(parts)


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
        # not used here ()
        self.format_prompt      = (self.gpt_cfg.get("format_prompt") or "").strip()

        # Slack-specific format prompts
        self.relay_format_prompt = (self.gpt_cfg.get("relay_format_prompt") or "").strip()
        self.post_format_prompt  = (self.gpt_cfg.get("post_format_prompt") or "").strip()

        if not self.relay_format_prompt:
            self.logger.warning("[config] empty relay_format_prompt")
        if not self.post_format_prompt:
            self.logger.warning("[config] empty post_format_prompt")

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

    # ------------- helpers -------------

    def _load_json(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _compose_relay_prompt(self, payload: Any) -> str:
        """
        Compose the prompt for Slack -> server relay.
        """
        personality = self.personality_prompt
        body = json.dumps(payload, ensure_ascii=False)
        return (
            f"{personality}\n"
            f"{self.relay_format_prompt}\n\n"
            f"Content:\n{body}\n"
        )

    def _compose_post_prompt(self, payload: Any) -> str:
        """
        Compose the prompt for server -> Slack post.
        """
        personality = self.personality_prompt
        body = json.dumps(payload, ensure_ascii=False)
        channel_clause = build_channel_policy_clause()

        return (
            f"{personality}\n"
            f"{self.post_format_prompt}\n\n"
            f"{channel_clause}\n\n"
            f"Content:\n{body}\n"
        )

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
agent = MyAgent(name="GPTSlackAgent")

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


# -------------------- Summoner handlers --------------------
# 1) Server -> Slack "post" path
@agent.receive(route="post")
async def recv_post(msg: Any) -> None:
    """
    Receive messages from the server that might result in Slack posts.
    We do not block, we just buffer content.
    """
    global from_server_buffer

    if from_server_buffer is None:
        return

    address = msg["remote_addr"]
    content = msg["content"]
    if content in [{}, None]:
        return

    await from_server_buffer.put(content)
    agent.logger.info(f"[recv:post] buffered content from SocketAddress={address}")


@agent.send(route="post")
async def send_post() -> Optional[Union[dict, str]]:
    """
    Non blocking send-side logic for posting to Slack.

    Pattern:
      - poll from_server_buffer non blocking
      - call GPT with post_format_prompt
      - if GPT says 'should_post': push into to_slack_buffer
      - return None (no message back to server needed)
    """
    global from_server_buffer, to_slack_buffer

    if from_server_buffer is None or to_slack_buffer is None:
        await asyncio.sleep(agent.sleep_seconds)
        return None

    if from_server_buffer.empty():
        await asyncio.sleep(agent.sleep_seconds)
        return None

    content = from_server_buffer.get_nowait()

    # GPT: decide how to post to Slack
    user_prompt = agent._compose_post_prompt(content)
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing="json",
        output_type=None,
        cost_limit=agent.cost_limit_usd,
        debug=agent.debug,
    )

    decision = result.get("output")
    await aprint("decision:", decision)

    if isinstance(decision, str):
        try:
            decision = json.loads(decision)
        except Exception as e:
            decision = {"_raw": decision, "parse_error": str(e)[:200]}
    elif not isinstance(decision, dict):
        decision = {}

    should_post = bool(decision.get("should_post", False))
    channel     = decision.get("channel")
    text        = decision.get("text")
    thread_ts   = decision.get("thread_ts")

    if should_post and channel and text:
        # Final safety: ignore non-allowed channels even if GPT misbehaves
        if normalize_post_channel(str(channel)) is None:
            agent.logger.info(f"[send:post] GPT picked disallowed channel={channel!r}; suppressing post.")
        else: 
            if should_post and channel and text:
                await to_slack_buffer.put(
                    {
                        "channel": channel,
                        "text": text,
                        "thread_ts": thread_ts,
                    }
                )
                agent.logger.info(f"[send:post] queued Slack message to channel={channel}")

    agent.logger.info(
        f"[send:post] model={agent.model} id={agent.my_id} cost={result.get('cost')}"
    )
    await asyncio.sleep(agent.sleep_seconds)
    # No need to send anything back to server
    return None


# 2) Slack -> server "relay" path
@agent.send(route="relay")
async def send_relay() -> Optional[Union[dict, str]]:
    global to_server_buffer, to_slack_buffer

    if to_server_buffer is None:
        await asyncio.sleep(agent.sleep_seconds)
        return None

    if to_server_buffer.empty():
        await asyncio.sleep(agent.sleep_seconds)
        return None

    slack_event = to_server_buffer.get_nowait()

    # 1) GPT: decide relay + slack reply
    user_prompt = agent._compose_relay_prompt(slack_event)
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing="json",
        output_type=None,
        cost_limit=agent.cost_limit_usd,
        debug=agent.debug,
    )

    decision = result.get("output")
    if isinstance(decision, str):
        try:
            decision = json.loads(decision)
        except Exception as e:
            decision = {"_raw": decision, "parse_error": str(e)[:200]}
    elif not isinstance(decision, dict):
        decision = {}

    relay          = bool(decision.get("relay", False))
    server_payload = decision.get("server_payload")
    slack_reply    = decision.get("slack_reply") or {}

    # 2) Always handle Slack reply if requested
    if to_slack_buffer is not None and isinstance(slack_reply, dict):
        if slack_reply.get("post", False):
            channel   = slack_reply.get("channel") or slack_event.get("channel")
            text      = slack_reply.get("text")
            thread_ts = slack_reply.get("thread_ts") or slack_event.get("ts")

            if channel and text:
                await to_slack_buffer.put(
                    {
                        "channel": channel,
                        "text": text,
                        "thread_ts": thread_ts,
                    }
                )
                agent.logger.info(f"[send:relay] queued slack_reply to channel={channel}")

    agent.logger.info(
        f"[send:relay] model={agent.model} id={agent.my_id} cost={result.get('cost')} relay={relay}"
    )

    await asyncio.sleep(agent.sleep_seconds)

    # 3) Only relay to server if appropriate
    if relay and isinstance(server_payload, dict) and server_payload:
        return server_payload

    return None

# -------------------- main --------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a Summoner client with a specified config.")
    parser.add_argument('--config', dest='config_path', required=False, help='The relative path to the client config (JSON), e.g., --config configs/client_config.json')
    args, _ = parser.parse_known_args()

    agent.loop.run_until_complete(setup())

    # Start Slack background tasks
    slack_socket_task = agent.loop.create_task(slack_socket_loop())
    slack_post_task   = agent.loop.create_task(slack_post_loop())

    try:
        agent.run(host="127.0.0.1", port=8888, config_path=args.config_path or "configs/client_config.json")
    finally:
        # Cancel background tasks
        for t in (slack_socket_task, slack_post_task):
            t.cancel()
        try:
            agent.loop.run_until_complete(
                asyncio.gather(slack_socket_task, slack_post_task, return_exceptions=True)
            )
        except Exception:
            pass
        agent.logger.info("Slack background tasks cancelled cleanly.")