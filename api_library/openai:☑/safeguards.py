import tiktoken
import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

def count_chat_tokens(
    messages: list[dict],
    model: str = "gpt-4o",
) -> int:
    """
    Returns the number of tokens that will be sent as 'prompt_tokens'
    for a chat.completions call with the given messages.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        # fallback to the base encoding if model isn't recognized
        encoding = tiktoken.get_encoding("cl100k_base")

    # overhead rules taken from the OpenAI cookbook
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
        # you can add other models here as they appear
        tokens_per_message = 3
        tokens_per_name = 1

    total_tokens = 0
    for msg in messages:
        total_tokens += tokens_per_message
        for key, val in msg.items():
            # encode each field value
            total_tokens += len(encoding.encode(val))
            if key == "name":
                total_tokens += tokens_per_name

    # every reply is primed with this many tokens for the assistant role
    total_tokens += 3
    return total_tokens


PRICING = {
    "gpt-3.5-turbo":     {"prompt": 0.0005,  "completion": 0.0015},
    "gpt-3.5-turbo-16k": {"prompt": 0.003,   "completion": 0.004},
    "gpt-4.1":           {"prompt": 0.002,   "completion": 0.008},
    "gpt-4.1-mini":      {"prompt": 0.0004,  "completion": 0.0016},
    "gpt-4.1-nano":      {"prompt": 0.0001,  "completion": 0.0004},
    "o3":                {"prompt": 0.002,   "completion": 0.008},
    "o4-mini":           {"prompt": 0.0011,  "completion": 0.0044},
    "gpt-4o":            {"prompt": 0.005,   "completion": 0.02},
    "gpt-4o-mini":       {"prompt": 0.0006,  "completion": 0.0024},
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
    try:
        rates = PRICING[model_name]
    except KeyError:
        raise ValueError(f"No pricing info for model '{model_name}'")
    return (
        prompt_tokens   / 1_000 * rates["prompt"]
      + max_completion_tokens / 1_000 * rates["completion"]
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
    try:
        rates = PRICING[model_name]
    except KeyError:
        raise ValueError(f"No pricing info for model '{model_name}'")
    return (
        prompt_tokens   / 1_000 * rates["prompt"]
      + completion_tokens / 1_000 * rates["completion"]
    )



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
    try:
        rate_per_1k = EMBEDDING_PRICING[model_name]
    except KeyError:
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
