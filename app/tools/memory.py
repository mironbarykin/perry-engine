import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

from app.cognee_adapter import CogneeUnavailable, recall_sync
from app.memory_vault import read_note, search_notes, vault_path
from app.storage import User
from app.tools.context import ToolContext


class MemorySearchParams(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=20)


class MemoryReadNoteParams(BaseModel):
    path: str = Field(min_length=1, max_length=300)


class MemoryWriteNoteParams(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20_000)

    @field_validator("path")
    @classmethod
    def markdown_path_only(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
            raise ValueError("path must be a relative .md path inside the user's memory vault")
        return value.replace("\\", "/")


class MemoryStatusParams(BaseModel):
    pass


def _schema(name: str, description: str, params: type[BaseModel]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params.model_json_schema(),
        },
    }


def memory_search(context: ToolContext, query: str, max_results: int = 5) -> str:
    try:
        results = recall_sync(context.user_id, vault_path(context.user_id), query, max_results)
    except CogneeUnavailable:
        return json.dumps(
            {
                "status": "cognee_unavailable",
                "message": "Cognee is not enabled or installed; use memory_status for indexing state.",
                "results": search_notes(context.user_id, query, max_results),
            }
        )
    return json.dumps({"status": "ok", "results": results})


def memory_read_note(context: ToolContext, path: str) -> str:
    return json.dumps({"path": path, "content": read_note(context.user_id, path)})


def memory_status(context: ToolContext, **_: object) -> str:
    return json.dumps(User.get_by_id(context.user_id).get_memory_status())


def memory_propose_write(context: ToolContext, path: str, content: str) -> str:
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "memory.write_note",
        {"path": path, "content": content},
        expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    )
    return json.dumps(
        {
            "status": "confirmation_required",
            "confirmation_id": confirmation_id,
            "path": path,
            "expires_in_minutes": 10,
            "message": "The proposed memory note requires explicit user confirmation.",
        }
    )


TOOLS = [
    (
        _schema(
            "memory_search",
            "Search this user's long-term Markdown memory for relevant facts and past conversations.",
            MemorySearchParams,
        ),
        MemorySearchParams,
        memory_search,
    ),
    (
        _schema(
            "memory_read_note",
            "Read one Markdown note from this user's memory vault after memory_search identifies its relative path.",
            MemoryReadNoteParams,
        ),
        MemoryReadNoteParams,
        memory_read_note,
    ),
    (
        _schema(
            "memory_status",
            "Report whether this user's Markdown memory files are waiting for indexing.",
            MemoryStatusParams,
        ),
        MemoryStatusParams,
        memory_status,
    ),
    (
        _schema(
            "memory_propose_write",
            "Propose a durable personal memory note. This never writes without explicit user confirmation.",
            MemoryWriteNoteParams,
        ),
        MemoryWriteNoteParams,
        memory_propose_write,
    ),
]
