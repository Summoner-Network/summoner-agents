from typing import Optional, Any
from dataclasses import dataclass
import tiktoken



def count_chat_tokens(
    messages: list[dict[str, str]],
    model: str = "gpt-4o",
) -> int:
    """
    Returns the number of tokens that will be sent as 'prompt_tokens'
    for a chat.completions call with the given messages.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        # Fallback if a brand-new model string is not yet mapped in tiktoken
        encoding = tiktoken.get_encoding("cl100k_base")

    # Overhead rules adapted from the OpenAI cookbook
    if model.startswith("gpt-3.5-turbo-0301"):
        tokens_per_message = 4
        tokens_per_name = -1
    elif model.startswith("gpt-3.5-turbo"):
        tokens_per_message = 4
        tokens_per_name = -1
    elif model.startswith("gpt-4"):
        tokens_per_message = 3
        tokens_per_name = 1
    else:
        # Default for newer families (4o, 5, etc.)
        tokens_per_message = 3
        tokens_per_name = 1

    total_tokens = 0
    for msg in messages:
        total_tokens += tokens_per_message
        for key, val in msg.items():
            # Encode each field value
            total_tokens += len(encoding.encode(val))
            if key == "name":
                total_tokens += tokens_per_name

    # Every reply is primed with this many tokens for the assistant role
    total_tokens += 3
    return total_tokens



# Per-1k token prices: {"prompt": <USD>, "completion": <USD>}
PRICING: dict[str, dict[str, float]] = {
    # Historical / legacy
    "gpt-3.5-turbo":     {"prompt": 0.0005,  "completion": 0.0015},
    "gpt-3.5-turbo-16k": {"prompt": 0.003,   "completion": 0.004},
    "gpt-4.1":           {"prompt": 0.002,   "completion": 0.008},
    "gpt-4.1-mini":      {"prompt": 0.0004,  "completion": 0.0016},
    "o3":                {"prompt": 0.002,   "completion": 0.008},
    "o4-mini":           {"prompt": 0.0011,  "completion": 0.0044},

    # 4o family
    "gpt-4o":            {"prompt": 0.0050,  "completion": 0.0200},
    "gpt-4o-mini":       {"prompt": 0.00015, "completion": 0.00060},

    # 5 family (example numbers; keep synced with docs)
    "gpt-5":             {"prompt": 0.00125, "completion": 0.01000},
    "gpt-5-mini":        {"prompt": 0.00025, "completion": 0.00200},
    "gpt-5-nano":        {"prompt": 0.00005, "completion": 0.00040},
}


def estimate_chat_request_cost(
    model_name: str,
    prompt_tokens: int,
    max_completion_tokens: int
) -> float:
    """
    Return the *estimated* cost (USD) if the model were to
    use prompt_tokens and then produce max_completion_tokens.
    """
    rates = PRICING.get(model_name)
    if not rates:
        raise ValueError(f"No pricing info for model '{model_name}'")
    return (
        (prompt_tokens / 1_000) * rates["prompt"]
        + (max_completion_tokens / 1_000) * rates["completion"]
    )


def actual_chat_request_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int
) -> float:
    """
    Return the *actual* cost (USD) once you know how many
    completion_tokens were consumed.
    """
    rates = PRICING.get(model_name)
    if not rates:
        raise ValueError(f"No pricing info for model '{model_name}'")
    return (
        (prompt_tokens / 1_000) * rates["prompt"]
        + (completion_tokens / 1_000) * rates["completion"]
    )


# Needed for newer openai models (gpt-5 family)
def normalize_usage(usage_obj: Any) -> Optional[dict[str, int]]:
    """
    Normalize usage from OpenAI SDK responses into:
      {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}
    Works for both Chat Completions and Responses API, when usage is present.
    Returns None if usage isn't available.
    """
    if usage_obj is None:
        return None

    # Try common shapes
    to_dict = None
    for attr in ("to_dict", "model_dump", "dict"):
        fn = getattr(usage_obj, attr, None)
        if callable(fn):
            try:
                to_dict = fn()
                break
            except Exception:
                pass

    if to_dict is None:
        if isinstance(usage_obj, dict):
            to_dict = usage_obj
        else:
            try:
                to_dict = dict(usage_obj)  # last resort
            except Exception:
                return None

    d = to_dict or {}

    # Chat Completions style
    if "prompt_tokens" in d or "completion_tokens" in d:
        prompt = int(d.get("prompt_tokens", 0))
        comp = int(d.get("completion_tokens", 0))
        total = int(d.get("total_tokens", prompt + comp))
        return {"prompt_tokens": prompt, "completion_tokens": comp, "total_tokens": total}

    # Responses API often uses input/output wording
    if "input_tokens" in d or "output_tokens" in d:
        prompt = int(d.get("input_tokens", 0))
        comp = int(d.get("output_tokens", 0))
        total = int(d.get("total_tokens", prompt + comp))
        return {"prompt_tokens": prompt, "completion_tokens": comp, "total_tokens": total}

    # Unknown/unsupported shape
    return None


@dataclass(frozen=True)
class Usage:
    """Unified usage view for both Chat Completions and Responses API."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


def get_usage_from_response(response: Any) -> Optional[Usage]:
    """
    Attempt to extract a unified Usage object from an OpenAI SDK response.
    Works for:
      - Chat Completions (response.usage has prompt/completion/total)
      - Responses API (usage may expose input/output/total)
    Returns None if usage isn't available.
    """
    usage_obj = getattr(response, "usage", None)
    if usage_obj is None:
        return None

    # Reuse your existing normalizer
    norm = normalize_usage(usage_obj)
    if norm is None:
        return None

    prompt = int(norm.get("prompt_tokens", 0))
    comp = int(norm.get("completion_tokens", 0))
    total = int(norm.get("total_tokens", prompt + comp))
    return Usage(prompt_tokens=prompt, completion_tokens=comp, total_tokens=total)



# Per-1k token input price for embeddings (input-only billing).
EMBEDDING_PRICING: dict[str, float] = {
    "text-embedding-3-small":  0.00002,
    "text-embedding-3-large":  0.00013,
    "text-embedding-ada-002":  0.00010,
}

def count_embedding_tokens(
    texts: list[str],
    model_name: str = "text-embedding-ada-002",
) -> int:
    """
    Returns the total number of tokens for a list of input strings
    when sent to the embeddings endpoint for model_name.
    """
    try:
        enc = tiktoken.encoding_for_model(model_name)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")

    # sum token counts for each string
    return sum(len(enc.encode(text)) for text in texts)


def estimate_embedding_request_cost(
    model_name: str,
    input_tokens: int,
) -> float:
    """
    Estimate the USD cost if the call used exactly input_tokens
    (e.g. your max or expected length).
    """
    rate_per_1k = EMBEDDING_PRICING.get(model_name)
    if rate_per_1k is None:
        raise ValueError(f"No embedding pricing on record for '{model_name}'")
    return input_tokens / 1_000 * rate_per_1k


def actual_embedding_request_cost(
    model_name: str,
    input_tokens: int,
) -> float:
    """
    Compute the USD cost once you know how many tokens were used.
    (Identical to estimate, since embeddings only bill for input.)
    """
    return estimate_embedding_request_cost(model_name, input_tokens)
