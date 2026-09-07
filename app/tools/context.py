from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    """Trusted request data injected by the engine, never chosen by the LLM."""

    user_id: str
