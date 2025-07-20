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