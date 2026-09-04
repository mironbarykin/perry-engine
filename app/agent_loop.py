from app.config import settings
from app.llm_client import call_model
from app.memory import append_turn, get_core_profile, get_working_memory
from app.models import Turn

SYSTEM_PROMPT_TEMPLATE = """You are Perry, {user_name}'s personal assistant.

What you know about {user_name}:
{profile}

Be concise and direct. Reply in whatever language {user_name} writes in."""


def build_messages(user_id: str, user_message: str) -> list[dict]:
    profile = get_core_profile(user_id)
    system = SYSTEM_PROMPT_TEMPLATE.format(user_name="Miron", profile=profile)

    messages = [{"role": "system", "content": system}]
    for turn in get_working_memory(user_id):
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user_message})
    return messages


def run_agent_loop(user_id: str, user_message: str) -> str:
    messages = build_messages(user_id, user_message)

    # No tools registered yet, so this always resolves on the first call.
    # Once tools exist: register their schemas, pass them into call_model,
    # then on each iteration check message.tool_calls — if present, dispatch
    # each one (via the MCP client manager), append the tool result as a
    # "tool" role message, and loop again. max_loop_iterations bounds how
    # many round trips a single request can take, so a confused model can't
    # spiral into an expensive loop.
    reply = "Sorry, I couldn't come up with a reply."
    for _ in range(settings.max_loop_iterations):
        message = call_model(messages)
        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            reply = message.content or reply
            break
        # Tool dispatch will go here once tools exist.
        break

    append_turn(user_id, Turn(role="user", content=user_message))
    append_turn(user_id, Turn(role="assistant", content=reply))
    return reply
