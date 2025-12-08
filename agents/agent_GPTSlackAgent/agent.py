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
from slack_sdk.errors import SlackApiError

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

# For GPT-shaped Slack posts
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

load_dotenv()

# Channels where posting failed (e.g. not_in_channel / channel_not_found)
# – used to ignore events and posts
NOT_ALLOWED_CHANNEL_IDS: set[str] = set()

# Channels where posting SUCCEEDED at least once
# – dynamic allowlist used in the GPT prompt
ALLOWED_CHANNEL_TOKENS: set[str] = set()


def normalize_post_channel(raw: str) -> Optional[str]:
    """
    Turn a user/GPT channel token into a Slack channel ID/name we may post to.

    Normalization:
      - Strip a single leading '#' if present (Slack expects 'general' or 'C123..',
        not '#general' / '#C123..').
      - Then check the NOT_ALLOWED_CHANNEL_IDS set.
      - Otherwise, be optimistic and let Slack teach us via errors.
    """
    if not raw:
        return None

    # Strip one leading '#' – this makes '#general' -> 'general', '#C123' -> 'C123'
    token = raw[1:] if raw.startswith("#") else raw

    # Hard block: channels we already learned are unusable
    if token in NOT_ALLOWED_CHANNEL_IDS:
        return None

    return token


async def slack_handle_events(sm_client: SocketModeClient, req: SocketModeRequest):
    """
    Main handler for everything Slack sends over Socket Mode.

    We keep it as thin as possible: normalize events and push them
    into to_server_buffer. GPT decisions happen in @send('relay').

    We react to app_mention events in any channel that is not explicitly
    marked as NOT_ALLOWED_CHANNEL_IDS.
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

    # Only react to explicit mentions of the bot (no generic "message" events)
    if event_type != "app_mention" or event.get("bot_id"):
        return

    text      = (event.get("text") or "").strip()
    channel   = event.get("channel")
    user      = event.get("user")
    ts        = event.get("ts")
    # Prefer the thread head if present, otherwise use this message ts
    thread_ts = event.get("thread_ts") or ts

    if not (channel and user and text and ts):
        return

    # 1) Hard block: channels that we know are not usable
    if channel in NOT_ALLOWED_CHANNEL_IDS:
        agent.logger.info(f"[slack] ignoring app_mention from NOT_ALLOWED channel={channel}")
        return

    # 2) Dedupe (same channel+ts can come more than once)
    if SEEN_SLACK_EVENTS is not None:
        key = (channel, ts)
        if key in SEEN_SLACK_EVENTS:
            agent.logger.info(f"[slack] duplicate app_mention channel={channel} ts={ts}; ignoring")
            return
        SEEN_SLACK_EVENTS.add(key)

    payload = {
        "source": "slack",
        "event_type": event_type,
        "channel": channel,
        "user": user,
        "ts": ts,
        "thread_ts": thread_ts,
        "text": text,
    }

    if to_server_buffer is not None:
        await to_server_buffer.put(payload)
    agent.logger.info(
        f"[slack] buffered app_mention from user={user} channel={channel}"
    )


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
            agent.logger.info(f"[slack_post_loop] suppressed post to disallowed/unknown channel={raw_channel!r}")
            continue

        try:
            kwargs = {"channel": channel, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            await SLACK_WEB_CLIENT.chat_postMessage(**kwargs)
            agent.logger.info(f"[slack_post_loop] posted to channel={channel}")

            # Learn this channel as allowed on successful post
            ALLOWED_CHANNEL_TOKENS.add(channel)

        except Exception as e:
            agent.logger.warning(
                f"[slack_post_loop] failed to post: {type(e).__name__}: {e}"
            )

            # Learn from Slack's error
            if isinstance(e, SlackApiError):
                err = (e.response.get("error") or "").strip()
                if err in ("not_in_channel", "channel_not_found"):
                    # Mark that channel as not allowed so we never try again
                    NOT_ALLOWED_CHANNEL_IDS.add(channel)
                    # If we previously thought it was allowed, forget it
                    ALLOWED_CHANNEL_TOKENS.discard(channel)
                    agent.logger.info(
                        f"[slack_post_loop] learned {err} for channel_id={channel}; "
                        f"future posts and events for this channel will be suppressed."
                    )


def build_channel_policy_clause() -> str:
    """
    Describe known allowed and blocked channels to the model in natural language.

    We keep this short and machine-like to reduce fuzzy matching:
    - Explicit list of blocked tokens, with character-by-character spelling.
    - Dynamic list of allowed tokens (channels that succeeded at least once).
    - Clear rule that blocking only happens on exact, character-level matches.
    - Clear instruction: write the reason first, then set 'should_post' based on it.
    """
    parts: list[str] = []

    allowed = sorted(ALLOWED_CHANNEL_TOKENS)
    blocked = sorted(NOT_ALLOWED_CHANNEL_IDS)

    # ------------------------------------------------------------------
    # Blocked channels: printed with character-by-character spelling
    # to force exact matching in the model's reasoning.
    # ------------------------------------------------------------------
    if blocked:
        blocked_lines = [
            f"- {ch}: " + ", ".join(ch)
            for ch in blocked
        ]
        blocked_str = "\n".join(blocked_lines)
        parts.append(
            "<blocked_channels>\n"
            "Blocked Slack channels. This is a sensitive field: channel names with slight variation "
            "must NOT be blocked by mistake.\n"
            f"{blocked_str}\n"
            "You must treat a channel as blocked only when the Content channel token matches one of "
            "these entries EXACTLY, character by character. Similarity or partial matches do not count.\n"
            "</blocked_channels>"
        )

    # ------------------------------------------------------------------
    # Allowed channels: dynamic allowlist (channels that have succeeded).
    # If empty, we fall back to the rule “any non-blocked channel is allowed”.
    # ------------------------------------------------------------------
    if allowed:
        allowed_str = ", ".join(allowed)
        parts.append(
            "<allowed_channels>\n"
            "Allowed Slack channels (these are known to be safe to post to): "
            f"{allowed_str}.\n"
            "If a channel name appears both here and in the blocked list, the blocked status takes precedence.\n"
            "</allowed_channels>"
        )
    else:
        parts.append(
            "<allowed_channels>\n"
            "There is currently no explicit allowlist. Any channel that is not blocked MAY be used, "
            "as long as it does not exactly match a blocked token.\n"
            "</allowed_channels>"
        )

    # ------------------------------------------------------------------
    # Global decision rule: reason first, then should_post.
    # This is the key pattern that stabilizes the model.
    # ------------------------------------------------------------------
    parts.append(
        "Channel decision rules:\n"
        "- ANY channel that is not blocked can be posted into. A channel name or ID should only be blocked "
        "if it corresponds EXACTLY to a blocked name/ID.\n"
        "  Example: #helloworld and #helloworlds are different because of the extra character 's'.\n"
        "- ALWAYS provide a brief 'reason' for why you accept or block a channel. The reason must appear "
        "BEFORE the 'should_post' field in the JSON you return.\n"
        "- Base the 'should_post' value strictly on that reason:\n"
        "    * If the Content channel matches a blocked channel token exactly (character-by-character), "
        "then should_post must be false.\n"
        "    * If it does NOT match any blocked token exactly, then you may set should_post to true, "
        "subject to the rest of the instructions.\n"
        "- When explaining your reason, you MUST spell the Content's channel token character-by-character and "
        "explicitly say whether it matches the spelling of a blocked channel exactly.\n"
        "- If it does not match character-by-character, you have it wrong and should treat the channel as allowed.\n"
        "- Ambiguous similarity or naming resemblance (for example, #summoner-tests vs #summonerbot-tests) is NOT a "
        "valid reason for blocking. Only exact matches justify blocking."
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
            raise ValueError(
                f"Invalid model in gpt_config.json: {self.model}. "
                f"Available: {', '.join(model_ids)}"
            )

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
            messages_str = str(message)
            long_messages = len(messages_str) > 20000
            await aprint(f"\033[92mInput: {messages_str[:20000] + '...' * long_messages}\033[0m")

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

    handoff: dict = content.pop("handoff", {}) if isinstance(content, dict) else {}

    user_id = None
    request_thread_ts = None

    # ------------------------------------------------------------------
    # Pre-scan for explicit target Slack channels and skip GPT if any of
    # them are already known to be NOT_ALLOWED.
    # ------------------------------------------------------------------
    explicit_channels: set[str] = set()

    if isinstance(content, dict):
        # 1) From explicit handoff (canonical source)
        if handoff and isinstance(handoff, dict) and agent.name in handoff:
            slack_handoff = handoff[agent.name]
            ch = slack_handoff.get("channel")
            if isinstance(ch, str):
                explicit_channels.add(ch)

        # 2) From content["slack_channel"], if your server uses this convention
        ch2 = content.get("slack_channel")
        if isinstance(ch2, str):
            explicit_channels.add(ch2)

        # 3) From content["slack"]["channel"], if present
        slack_meta = content.get("slack")
        if isinstance(slack_meta, dict):
            ch3 = slack_meta.get("channel")
            if isinstance(ch3, str):
                explicit_channels.add(ch3)

    if any(ch in NOT_ALLOWED_CHANNEL_IDS for ch in explicit_channels):
        agent.logger.info(
            f"[send:post] skipping GPT call for NOT_ALLOWED channels={explicit_channels!r}"
        )
        await asyncio.sleep(agent.sleep_seconds)
        return None
    # ------------------------------------------------------------------

    if isinstance(content, dict):
        # strip routing metadata
        prompt_payload = {k: v for k, v in content.items() if k not in ["to", "from"]}

        # Add slack_channel_to_use if the handoff is present
        if handoff and isinstance(handoff, dict) and agent.name in handoff:
            slack_handoff = handoff[agent.name]
            slack_channel = slack_handoff.get("channel")
            user_request = slack_handoff.get("original_user_request")
            user_id = slack_handoff.get("original_user")
            request_thread_ts = slack_handoff.get("thread_ts")

            if user_request:
                prompt_payload["original_user_request"] = f"You MUST respond to this user's message: {user_request}"


            if isinstance(slack_channel, str):
                # At this point we already screened NOT_ALLOWED_CHANNEL_IDS above;
                # if we got here, the channel is not known-bad.
                prompt_payload["slack_channel"] = slack_channel
    else:
        prompt_payload = content

    # GPT: decide how to post to Slack
    user_prompt = agent._compose_post_prompt(prompt_payload)
    result = await agent.gpt_call_async(
        message=user_prompt,
        model_name=agent.model,
        output_parsing="json",
        output_type=None,
        cost_limit=agent.cost_limit_usd,
        debug=agent.debug,
    )

    decision = result.get("output")
    if agent.debug:
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
    thread_ts   = request_thread_ts or decision.get("thread_ts")

    if should_post and channel and text:
        # Final safety: ignore blocked channels even if GPT misbehaves
        if normalize_post_channel(str(channel)) is None:
            agent.logger.info(
                f"[send:post] GPT picked blocked/invalid channel={channel!r}; suppressing post."
            )
        else:
            await to_slack_buffer.put(
                {
                    "channel": channel,
                    "text": f"<@{user_id}> " + text if user_id and request_thread_ts is None else text,
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
    await aprint(slack_event)

    # Canonical source information from Slack event
    event_channel   = slack_event.get("channel")    # channel ID
    event_text      = slack_event.get("text")
    event_user      = slack_event.get("user")
    # Prefer thread_ts when present, otherwise fall back to this message ts
    event_thread_ts = slack_event.get("thread_ts") or slack_event.get("ts")

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
    if agent.debug:
        await aprint("decision:", decision)

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

    # 2) Immediate Slack acknowledgement
    if to_slack_buffer is not None and isinstance(slack_reply, dict):
        if slack_reply.get("post", False):
            reply_channel = slack_reply.get("channel") or event_channel
            text          = slack_reply.get("text")
            # Prefer GPT's explicit thread_ts, otherwise use the original thread
            thread_ts     = slack_reply.get("thread_ts") or event_thread_ts

            if reply_channel and text:
                await to_slack_buffer.put(
                    {
                        "channel": reply_channel,
                        "text": text,
                        "thread_ts": thread_ts,
                    }
                )
                agent.logger.info(
                    f"[send:relay] queued slack_reply to channel={reply_channel}"
                )

    agent.logger.info(
        f"[send:relay] model={agent.model} id={agent.my_id} "
        f"cost={result.get('cost')} relay={relay}"
    )

    await asyncio.sleep(agent.sleep_seconds)

    # 3) Only relay to server if appropriate, with explicit handoff
    if relay and isinstance(server_payload, dict) and server_payload and event_channel:
        server_payload["handoff"] = {
            agent.name: {
                "channel": event_channel,
                "original_user_request": event_text,
                "original_user": event_user,
                "thread_ts": event_thread_ts,
            }
        }
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
            agent.loop.run_until_complete(asyncio.gather(slack_socket_task, slack_post_task, return_exceptions=True))
        except Exception:
            pass
        agent.logger.info("Slack background tasks cancelled cleanly.")
