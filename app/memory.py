from collections import defaultdict, deque

from app.config import settings
from app.models import Turn

# In-memory, per-process only — fine for local dev, not for multiple workers
# or a restart-safe deploy. Swap for Redis or a Postgres table with the same
# get/append interface once this needs to survive a restart or scale out.
_working_memory: dict[str, deque[Turn]] = defaultdict(
    lambda: deque(maxlen=settings.max_working_turns)
)


def get_working_memory(user_id: str) -> list[Turn]:
    return list(_working_memory[user_id])


def append_turn(user_id: str, turn: Turn) -> None:
    _working_memory[user_id].append(turn)
    # TODO: once the deque is full, the oldest turn about to be evicted could
    # be folded into a running summary (one cheap-model call) instead of just
    # dropped, so long sessions don't lose earlier context entirely. Worth
    # adding once conversations regularly run past max_working_turns.


def get_core_profile(user_id: str) -> str:
    """
    Small, curated, always-on facts about the user — kept short on purpose,
    since this gets injected into every single request. Anything bigger than
    a few sentences belongs in Cognee instead, exposed as a tool the agent
    calls when the topic actually comes up (diet, workouts, dating notes,
    etc.) rather than forced into every prompt.
    """
    # TODO: load this from a small structured store (or a dedicated Cognee
    # "profile" node) instead of hardcoding it.
    profile = (
        "Born on 24th of April 2007 in Moscow, Russia. Lived for 15 years in Khimki, Russia. Moved to Düsseldorf, Germany in 2022, lives in Meerbusch, Germany."
        "Speaks Russian, English, German. Has a strong interest in basketball, programming, finance and self-improvement. Came out as gay in 2023."
        "Currently studying in Theodor-Flidner-Gymnasium in Düsseldorf, Germany (finishes in 2027 with Abitur). Plans to study Computer Science at university in Europe."
        "Plays basketball since 2014, referees and coaches since 2023, has a dream of becoming a EuroLeague basketball referee."
        "And later working in (maybe his own) Tech Company, traveling the world, and living a happy and rich life with a loving partner and family, alongside officiating."
    )
    return profile[: settings.max_profile_chars]
