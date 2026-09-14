# Add each user-scoped tool module here. A tool function receives a trusted
# ToolContext; it never accepts a user ID from the LLM's arguments.
from app.tools import basketball, calendar, tasks

_TOOLS = [*basketball.TOOLS, *calendar.TOOLS, *tasks.TOOLS]

TOOLS = [schema for schema, _, _ in _TOOLS]
FUNCTIONS = {schema["function"]["name"]: run for schema, _, run in _TOOLS}
PARAMS_MODELS = {schema["function"]["name"]: params for schema, params, _ in _TOOLS}

# todo:
# 1. basketball tools, ✅ getting leagues games, getting umbesetzungen
# 2. finance tools, getting stock prices, getting crypto prices
# 3. language learning tools, getting a word/phrase of the day
# 4. scheduling tools, 
# 5. google tools (map,✅calendar,✅tasks)
