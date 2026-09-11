import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, model_validator

from app.integrations.google import get_tasks_service
from app.storage import User
from app.tools.context import ToolContext


class ListTaskListsParams(BaseModel):
    pass


class ListTasksParams(BaseModel):
    tasklist_id: str = "@default"
    show_completed: bool = False
    max_results: int = Field(default=50, ge=1, le=100)


class CreateTaskParams(BaseModel):
    tasklist_id: str = "@default"
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=8000)
    due: datetime | None = None


class UpdateTaskParams(BaseModel):
    tasklist_id: str = "@default"
    task_id: str = Field(min_length=1, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=8000)
    due: datetime | None = None
    status: str | None = Field(default=None, pattern="^(needsAction|completed)$")

    @model_validator(mode="after")
    def has_changes(self) -> "UpdateTaskParams":
        if all(value is None for value in (self.title, self.notes, self.due, self.status)):
            raise ValueError("at least one task field must be changed")
        return self


def _schema(name: str, description: str, params: type[BaseModel]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params.model_json_schema()}}


def list_task_lists(context: ToolContext, **_: object) -> str:
    response = get_tasks_service(context.user_id).tasklists().list(maxResults=100).execute()
    return json.dumps([{"id": item.get("id"), "title": item.get("title")} for item in response.get("items", [])])


def list_tasks(context: ToolContext, tasklist_id: str = "@default", show_completed: bool = False, max_results: int = 50) -> str:
    response = get_tasks_service(context.user_id).tasks().list(
        tasklist=tasklist_id, showCompleted=show_completed, showHidden=False, maxResults=max_results
    ).execute()
    return json.dumps(
        [{"id": item.get("id"), "title": item.get("title"), "notes": item.get("notes"),
          "due": item.get("due"), "status": item.get("status"), "completed": item.get("completed")}
         for item in response.get("items", [])]
    )


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(minutes=10)).isoformat()


def propose_task(context: ToolContext, tasklist_id: str, title: str, notes: str | None = None, due: datetime | None = None) -> str:
    task = {"title": title}
    if notes is not None:
        task["notes"] = notes
    if due is not None:
        task["due"] = due.isoformat()
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "tasks.create_task", {"tasklist_id": tasklist_id, "task": task}, _expires_at()
    )
    return json.dumps({"status": "confirmation_required", "confirmation_id": confirmation_id, "expires_in_minutes": 10, "task": task})


def propose_task_update(context: ToolContext, tasklist_id: str, task_id: str, title: str | None = None,
                        notes: str | None = None, due: datetime | None = None, status: str | None = None) -> str:
    changes = {key: value for key, value in {"title": title, "notes": notes, "status": status}.items() if value is not None}
    if due is not None:
        changes["due"] = due.isoformat()
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "tasks.update_task", {"tasklist_id": tasklist_id, "task_id": task_id, "changes": changes}, _expires_at()
    )
    return json.dumps({"status": "confirmation_required", "confirmation_id": confirmation_id, "expires_in_minutes": 10, "changes": changes})


LIST_TASK_LISTS_SCHEMA = _schema("tasks_list_task_lists", "List the user's Google Task lists.", ListTaskListsParams)
LIST_TASKS_SCHEMA = _schema("tasks_list_tasks", "List tasks from a Google Task list.", ListTasksParams)
CREATE_TASK_SCHEMA = _schema("tasks_propose_task", "Prepare a Google Task; it is not created until the user confirms outside the LLM.", CreateTaskParams)
UPDATE_TASK_SCHEMA = _schema("tasks_propose_task_update", "Prepare changes to a Google Task; no change is made until the user confirms outside the LLM.", UpdateTaskParams)

TOOLS = [
    (LIST_TASK_LISTS_SCHEMA, ListTaskListsParams, list_task_lists),
    (LIST_TASKS_SCHEMA, ListTasksParams, list_tasks),
    (CREATE_TASK_SCHEMA, CreateTaskParams, propose_task),
    (UPDATE_TASK_SCHEMA, UpdateTaskParams, propose_task_update),
]
