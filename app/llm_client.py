from openai import OpenAI

from app.config import settings

# OpenRouter is OpenAI-compatible, so the official `openai` client works
# unchanged — just point base_url at OpenRouter and use its model IDs
# (e.g. "anthropic/claude-haiku-4.5", "deepseek/deepseek-chat", ...).
client = OpenAI(
    base_url=settings.openrouter_base_url,
    api_key=settings.openrouter_api_key,
)


def call_model(
    messages: list,
    model: str | None = None,
    tools: list = [],
):
    """
    Call the LLM with the given messages, model, and tools.
    Single point of contact with the LLM. Every other module calls this
    instead of touching the client directly, so the model — or even the
    provider — is a one-line change here, not a search-and-replace.
    """
    response = client.chat.completions.create(
        model=model or settings.default_model,
        messages=messages,
        tools=tools,
    )
    return response.choices[0].message
