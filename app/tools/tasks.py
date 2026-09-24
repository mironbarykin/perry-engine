import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from googleapiclient.errors import HttpError

from app.integrations.google import get_tasks_service
from app.storage import User
from app.tools.context import ToolContext


class ListTaskListsParams(BaseModel):
    pass


class ListTasksParams(BaseModel):
    """Use tasklist_id='*' to search every task list."""

    tasklist_id: str | None = "@default"
    show_completed: bool = False
    due_min: datetime | None = None
    due_max: datetime | None = None
    max_results: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def valid_range(self) -> "ListTasksParams":
        if self.due_min and self.due_max and self.due_max <= self.due_min:
            raise ValueError("due_max must be after due_min")
        return self


class TasksCrudParams(BaseModel):
    operation: Literal["create", "read", "update", "delete"]
    tasklist_id: str = "@default"
    task_id: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=8000)
    due: datetime | None = None
    status: str | None = Field(default=None, pattern="^(needsAction|completed)$")

    @model_validator(mode="after")
    def validate_operation(self) -> "TasksCrudParams":
        if self.operation in {"read", "update", "delete"} and not self.task_id:
            raise ValueError(f"task_id is required for {self.operation}")
        if self.operation == "create" and not self.title:
            raise ValueError("title is required to create a task")
        if self.operation == "update" and all(
            value is None for value in (self.title, self.notes, self.due, self.status)
        ):
            raise ValueError("at least one task field is required for update")
        return self


def _schema(name: str, description: str, params: type[BaseModel]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": params.model_json_schema()}}


def _task_lists(service) -> list[dict]:
    lists: list[dict] = []
    page_token: str | None = None
    while True:
        response = service.tasklists().list(maxResults=100, pageToken=page_token).execute()
        lists.extend(item for item in response.get("items", []) if item.get("id"))
        page_token = response.get("nextPageToken")
        if not page_token:
            return lists


def list_task_lists(context: ToolContext, **_: object) -> str:
    service = get_tasks_service(context.user_id)
    return json.dumps([{"id": item["id"], "title": item.get("title")} for item in _task_lists(service)])


def _resolve_tasklist_id(service, tasklist_id: str) -> str:
    lists = _task_lists(service)
    if tasklist_id == "@default":
        if not lists:
            raise ValueError("No Google Task lists are available")
        return str(lists[0]["id"])
    if any(item["id"] == tasklist_id for item in lists):
        return tasklist_id
    raise ValueError(
        "Google Task list was not found. Use tasks_list_task_lists and select a current ID."
    )


def _task_item(item: dict, tasklist_id: str) -> dict:
    return {
        "id": item.get("id"),
        "tasklist_id": tasklist_id,
        "title": item.get("title"),
        "notes": item.get("notes"),
        "due": item.get("due"),
        "status": item.get("status"),
        "completed": item.get("completed"),
    }


def _due_in_range(due: str | None, due_min: datetime | None, due_max: datetime | None) -> bool:
    if due is None:
        return due_min is None and due_max is None
    due_value = datetime.fromisoformat(due.replace("Z", "+00:00"))
    if due_value.tzinfo is None:
        due_value = due_value.replace(tzinfo=UTC)
    if due_min and due_min.tzinfo is None:
        due_min = due_min.replace(tzinfo=UTC)
    if due_max and due_max.tzinfo is None:
        due_max = due_max.replace(tzinfo=UTC)
    if due_min and due_value < due_min:
        return False
    if due_max and due_value > due_max:
        return False
    return True


def list_tasks(
    context: ToolContext,
    tasklist_id: str | None = "@default",
    show_completed: bool = False,
    due_min: datetime | None = None,
    due_max: datetime | None = None,
    max_results: int = 50,
) -> str:
    service = get_tasks_service(context.user_id)
    try:
        list_ids = (
            [_resolve_tasklist_id(service, tasklist_id or "@default")]
            if tasklist_id != "*"
            else [str(item["id"]) for item in _task_lists(service)]
        )
        tasks: list[dict] = []
        for resolved_id in list_ids:
            page_token: str | None = None
            while len(tasks) < max_results:
                response = service.tasks().list(
                    tasklist=resolved_id,
                    showCompleted=show_completed,
                    showHidden=False,
                    maxResults=min(100, max_results - len(tasks)),
                    pageToken=page_token,
                ).execute()
                for item in response.get("items", []):
                    if _due_in_range(item.get("due"), due_min, due_max):
                        tasks.append(_task_item(item, resolved_id))
                    if len(tasks) >= max_results:
                        return json.dumps(tasks)
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
    except (HttpError, ValueError) as exc:
        if isinstance(exc, HttpError) and exc.resp.status != 404:
            raise
        return json.dumps(
            {
                "status": "invalid_tasklist",
                "message": str(exc),
                "action": "Call tasks_list_task_lists and retry with a current tasklist_id.",
            }
        )
    return json.dumps(tasks)


def get_task(context: ToolContext, tasklist_id: str, task_id: str) -> str:
    service = get_tasks_service(context.user_id)
    resolved_id = _resolve_tasklist_id(service, tasklist_id)
    item = service.tasks().get(tasklist=resolved_id, task=task_id).execute()
    return json.dumps(_task_item(item, resolved_id))


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(minutes=10)).isoformat()


def propose_task(context: ToolContext, tasklist_id: str, title: str, notes: str | None = None, due: datetime | None = None) -> str:
    tasklist_id = _resolve_tasklist_id(get_tasks_service(context.user_id), tasklist_id)
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
    tasklist_id = _resolve_tasklist_id(get_tasks_service(context.user_id), tasklist_id)
    changes = {key: value for key, value in {"title": title, "notes": notes, "status": status}.items() if value is not None}
    if due is not None:
        changes["due"] = due.isoformat()
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "tasks.update_task", {"tasklist_id": tasklist_id, "task_id": task_id, "changes": changes}, _expires_at()
    )
    return json.dumps({"status": "confirmation_required", "confirmation_id": confirmation_id, "expires_in_minutes": 10, "changes": changes})


def propose_task_delete(context: ToolContext, tasklist_id: str, task_id: str) -> str:
    resolved_id = _resolve_tasklist_id(get_tasks_service(context.user_id), tasklist_id)
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "tasks.delete_task",
        {"tasklist_id": resolved_id, "task_id": task_id},
        _expires_at(),
    )
    return json.dumps(
        {
            "status": "confirmation_required",
            "confirmation_id": confirmation_id,
            "expires_in_minutes": 10,
            "task_id": task_id,
        }
    )


def tasks_crud(
    context: ToolContext,
    operation: str,
    tasklist_id: str = "@default",
    task_id: str | None = None,
    title: str | None = None,
    notes: str | None = None,
    due: datetime | None = None,
    status: str | None = None,
) -> str:
    if operation == "read":
        return get_task(context, tasklist_id, task_id or "")
    if operation == "delete":
        return propose_task_delete(context, tasklist_id, task_id or "")
    if operation == "create":
        return propose_task(context, tasklist_id, title or "", notes, due)
    return propose_task_update(context, tasklist_id, task_id or "", title, notes, due, status)


LIST_TASK_LISTS_SCHEMA = _schema("tasks_list_task_lists", "List the user's Google Task lists.", ListTaskListsParams)
LIST_TASKS_SCHEMA = _schema("tasks_list_tasks", "List tasks from one list, or use tasklist_id='*' to search all lists; optionally filter by due_min/due_max.", ListTasksParams)
TASKS_CRUD_SCHEMA = _schema(
    "tasks_crud",
    "Perform exactly one task CRUD operation. Use create/read/update/delete. Read returns the task immediately; create/update/delete return a confirmation ID and never mutate Google Tasks directly.",
    TasksCrudParams,
)

TOOLS = [
    (LIST_TASK_LISTS_SCHEMA, ListTaskListsParams, list_task_lists),
    (LIST_TASKS_SCHEMA, ListTasksParams, list_tasks),
    (TASKS_CRUD_SCHEMA, TasksCrudParams, tasks_crud),
]
