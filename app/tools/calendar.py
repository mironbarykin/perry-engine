import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.integrations.google import (
    GoogleIntegrationError,
    get_calendar_service,
    get_shared_calendar_service,
)
from app.storage import User
from app.tools.context import ToolContext


class ListCalendarsParams(BaseModel):
    pass


class ListEventsParams(BaseModel):
    """List all matching events; set max_results only to limit tool output."""

    time_min: datetime | None = None
    time_max: datetime | None = None
    include_shared: bool = False
    max_results: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def valid_range(self) -> "ListEventsParams":
        if self.time_min and self.time_max and self.time_max <= self.time_min:
            raise ValueError("time_max must be after time_min")
        return self


class CalendarCrudParams(BaseModel):
    operation: Literal["create", "read", "update", "delete"]
    calendar_id: str = "primary"
    event_id: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=250)
    start: datetime | None = None
    end: datetime | None = None
    description: str | None = Field(default=None, max_length=4000)
    location: str | None = Field(default=None, max_length=500)
    color_id: int | None = Field(default=None, ge=1, le=11)
    color_name: Literal[
        "lavender", "sage", "grape", "flamingo", "banana", "tangerine",
        "peacock", "graphite", "blueberry", "basil", "tomato",
    ] | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> "CalendarCrudParams":
        if self.operation in {"read", "delete"} and not self.event_id:
            raise ValueError(f"event_id is required for {self.operation}")
        if self.operation == "create":
            if not self.title or self.start is None or self.end is None:
                raise ValueError("title, start, and end are required to create an event")
        if self.operation == "update":
            if not self.event_id:
                raise ValueError("event_id is required for update")
            if all(value is None for value in (self.title, self.start, self.end, self.description, self.location, self.color_id, self.color_name)):
                raise ValueError("at least one event field is required for update")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("end must be after start")
        return self


LIST_CALENDARS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_list_calendars",
        "description": "List the user's Google calendars and their IDs before selecting one.",
        "parameters": ListCalendarsParams.model_json_schema(),
    },
}

LIST_EVENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_list_events",
        "description": "List all events in the requested time range from every calendar in the authenticated user's Google account. Results are paginated internally, so max_results is optional and unset means no artificial result limit. Shared/service-account calendars are excluded unless include_shared=true. Each result has color_id, color (background hex), and color_name.",
        "parameters": ListEventsParams.model_json_schema(),
    },
}

CALENDAR_CRUD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar_crud",
        "description": "Perform exactly one calendar CRUD operation. Use create/read/update/delete. color_id is Google's 1-11 event color; color_name is optional. Read returns the event immediately; create/update/delete return a confirmation ID.",
        "parameters": CalendarCrudParams.model_json_schema(),
    },
}

GOOGLE_COLOR_NAMES = {
    "1": "lavender",
    "2": "sage",
    "3": "grape",
    "4": "flamingo",
    "5": "banana",
    "6": "tangerine",
    "7": "peacock",
    "8": "graphite",
    "9": "blueberry",
    "10": "basil",
    "11": "tomato",
}
COLOR_NAME_TO_ID = {name: int(color_id) for color_id, name in GOOGLE_COLOR_NAMES.items()}


def _calendar_item(item: dict) -> dict:
    color_id = item.get("colorId")
    return {
        "id": item.get("id"),
        "name": item.get("summary") or item.get("id"),
        "description": item.get("description"),
        "timezone": item.get("timeZone"),
        "primary": item.get("primary", False),
        "color_id": color_id,
        "color_name": GOOGLE_COLOR_NAMES.get(str(color_id)) if color_id else None,
        "background_color": item.get("backgroundColor"),
        "foreground_color": item.get("foregroundColor"),
    }


def list_calendars(context: ToolContext, **_: object) -> str:
    links = User.get_by_id(context.user_id).get_calendar_links()
    shared_ids = {
        str(link["calendar_id"]) for link in links if link["access_mode"] == "readonly"
    }
    calendars: list[dict] = []
    try:
        response = get_calendar_service(context.user_id).calendarList().list().execute()
        linked_by_id = {str(link["calendar_id"]): link for link in links}
        calendars.extend(
            {
                **_calendar_item(item),
                "name": str(
                    linked_by_id[str(item["id"])]["display_name"]
                    if str(item.get("id")) in linked_by_id
                    else item.get("summary") or item.get("id")
                ),
                "linked_to_user": str(item.get("id")) in linked_by_id,
                "access_mode": next(
                    (link["access_mode"] for link in links if link["calendar_id"] == item.get("id")),
                    "read_write",
                ),
            }
            for item in response.get("items", [])
        )
    except GoogleIntegrationError:
        if not shared_ids:
            raise
    if shared_ids:
        linked_by_id = {str(link["calendar_id"]): link for link in links}
        shared_service = get_shared_calendar_service()
        shared_response = shared_service.calendarList().list().execute()
        calendars.extend(
            {
                **_calendar_item(item),
                "id": f"shared:{item['id']}",
                "name": str(linked_by_id[item["id"]]["display_name"]),
                "shared_readonly": True,
                "linked_to_user": True,
            }
            for item in shared_response.get("items", [])
            if item.get("id") in shared_ids
        )
    return json.dumps(calendars)


def _event_item(
    item: dict,
    calendar_name: str,
    event_colors: dict | None = None,
    calendar_color: dict | None = None,
    calendar_palette: dict | None = None,
) -> dict:
    event_color_id = item.get("colorId")
    if event_color_id is not None:
        color_id = int(event_color_id)
        color_data = event_colors.get(str(event_color_id), {}) if event_colors else {}
    else:
        default_color_id = calendar_color.get("color_id") if calendar_color else None
        color_id = int(default_color_id) if default_color_id is not None else None
        color_data = calendar_color.get("colors", {}) if calendar_color else {}
    color = color_data.get("background")
    return {
        "id": item.get("id"),
        "calendar": calendar_name,
        "title": item.get("summary"),
        "start": item.get("start"),
        "end": item.get("end"),
        "location": item.get("location"),
        "description": item.get("description"),
        "status": item.get("status"),
        "color_id": color_id,
        "color": color,
        "color_name": _color_name(
            color_id,
            color,
            event_colors if event_color_id is not None else calendar_palette,
        ),
    }


def _color_name(
    color_id: int | None,
    color: str | None,
    palette: dict | None,
) -> str | None:
    """Name only a color whose returned hex matches Google's palette."""
    if color_id is None or not color or not palette:
        return None
    palette_color = palette.get(str(color_id), {}).get("background")
    if palette_color and palette_color.lower() == color.lower():
        return GOOGLE_COLOR_NAMES.get(str(color_id))
    return None


def list_events(
    context: ToolContext,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
    include_shared: bool = False,
    max_results: int | None = None,
) -> str:
    now = datetime.now(UTC)
    links = User.get_by_id(context.user_id).get_calendar_links()
    shared_ids = {
        str(link["calendar_id"])
        for link in links
        if link["access_mode"] == "readonly"
    } if include_shared else set()
    calendars = []
    service = None
    try:
        service = get_calendar_service(context.user_id)
        calendars.extend(
            (
                item["id"],
                item.get("summary") or item["id"],
                item.get("colorId"),
                {
                    "background": item.get("backgroundColor"),
                    "foreground": item.get("foregroundColor"),
                },
            )
            for item in service.calendarList().list().execute().get("items", [])
            if item.get("id")
        )
    except GoogleIntegrationError:
        if not shared_ids:
            raise
    if shared_ids:
        shared_service = get_shared_calendar_service()
        calendars.extend(
            (
                f"shared:{item['id']}",
                item.get("summary") or item["id"],
                item.get("colorId"),
                {
                    "background": item.get("backgroundColor"),
                    "foreground": item.get("foregroundColor"),
                },
            )
            for item in shared_service.calendarList().list().execute().get("items", [])
            if item.get("id") in shared_ids
        )
    events: list[dict] = []
    for current_calendar_id, calendar_name, calendar_color_id, calendar_colors in calendars:
        service_for_calendar = service
        if current_calendar_id.startswith("shared:"):
            current_calendar_id = current_calendar_id.removeprefix("shared:")
            service_for_calendar = get_shared_calendar_service()
        if service_for_calendar is None:
            raise GoogleIntegrationError("Google Calendar is not connected for this user")
        color_response = service_for_calendar.colors().get().execute()
        event_colors = color_response.get("event", {})
        calendar_palette = color_response.get("calendar", {})
        page_token: str | None = None
        calendar_events = 0
        while max_results is None or calendar_events < max_results:
            remaining = max_results - calendar_events if max_results is not None else 250
            response = (
                service_for_calendar.events()
                .list(
                    calendarId=current_calendar_id,
                    timeMin=(time_min or now).isoformat(),
                    timeMax=(time_max or now + timedelta(days=14)).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=min(250, remaining),
                    pageToken=page_token,
                )
                .execute()
            )
            normalized_events = [
                _event_item(
                    item,
                    calendar_name,
                    event_colors,
                    {
                        "color_id": calendar_color_id,
                        "colors": calendar_colors,
                    },
                    calendar_palette,
                )
                for item in response.get("items", [])
            ]
            events.extend(normalized_events)
            calendar_events += len(normalized_events)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    events.sort(key=lambda item: item["start"].get("dateTime", item["start"].get("date", "")))
    return json.dumps(events if max_results is None else events[:max_results])


def get_event(context: ToolContext, calendar_id: str, event_id: str) -> str:
    if calendar_id.startswith("shared:"):
        shared_ids = {
            str(link["calendar_id"])
            for link in User.get_by_id(context.user_id).get_calendar_links()
            if link["access_mode"] == "readonly"
        }
        if calendar_id.removeprefix("shared:") not in shared_ids:
            raise GoogleIntegrationError("This shared calendar is not linked to the user")
    service = get_shared_calendar_service() if calendar_id.startswith("shared:") else get_calendar_service(context.user_id)
    actual_calendar_id = calendar_id.removeprefix("shared:")
    item = service.events().get(calendarId=actual_calendar_id, eventId=event_id).execute()
    color_response = service.colors().get().execute()
    calendar_item = (
        service.calendarList().get(calendarId=actual_calendar_id).execute()
    )
    return json.dumps(
        _event_item(
            item,
            calendar_item.get("summary")
            or item.get("organizer", {}).get("displayName")
            or actual_calendar_id,
            color_response.get("event", {}),
            {
                "color_id": calendar_item.get("colorId"),
                "colors": {
                    "background": calendar_item.get("backgroundColor"),
                    "foreground": calendar_item.get("foregroundColor"),
                },
            },
            color_response.get("calendar", {}),
        )
    )


def _expires_at() -> str:
    return (datetime.now(UTC) + timedelta(minutes=10)).isoformat()


def propose_event(
    context: ToolContext,
    calendar_id: str,
    title: str,
    start: datetime,
    end: datetime,
    description: str | None = None,
    location: str | None = None,
    color_id: int | None = None,
    color_name: str | None = None,
) -> str:
    _ensure_writable_calendar(calendar_id)
    resolved_color_id = _resolve_color_id(color_id, color_name)
    event = {
        "summary": title,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if description is not None:
        event["description"] = description
    if location is not None:
        event["location"] = location
    if resolved_color_id is not None:
        event["colorId"] = resolved_color_id
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "calendar.create_event",
        {"calendar_id": calendar_id, "event": event},
        _expires_at(),
    )
    return json.dumps({"status": "confirmation_required", "confirmation_id": confirmation_id, "expires_in_minutes": 10, "event": event})


def propose_event_update(
    context: ToolContext,
    calendar_id: str,
    event_id: str,
    title: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    color_id: int | None = None,
    color_name: str | None = None,
) -> str:
    _ensure_writable_calendar(calendar_id)
    resolved_color_id = _resolve_color_id(color_id, color_name)
    changes: dict[str, object] = {}
    if title is not None:
        changes["summary"] = title
    if start is not None:
        changes["start"] = {"dateTime": start.isoformat()}
    if end is not None:
        changes["end"] = {"dateTime": end.isoformat()}
    if description is not None:
        changes["description"] = description
    if location is not None:
        changes["location"] = location
    if resolved_color_id is not None:
        changes["colorId"] = resolved_color_id
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "calendar.update_event",
        {"calendar_id": calendar_id, "event_id": event_id, "changes": changes},
        _expires_at(),
    )
    return json.dumps({"status": "confirmation_required", "confirmation_id": confirmation_id, "expires_in_minutes": 10, "changes": changes})


def propose_event_delete(context: ToolContext, calendar_id: str, event_id: str) -> str:
    _ensure_writable_calendar(calendar_id)
    confirmation_id = User.get_by_id(context.user_id).create_confirmation(
        "calendar.delete_event",
        {"calendar_id": calendar_id, "event_id": event_id},
        _expires_at(),
    )
    return json.dumps(
        {
            "status": "confirmation_required",
            "confirmation_id": confirmation_id,
            "expires_in_minutes": 10,
            "event_id": event_id,
        }
    )


def _ensure_writable_calendar(calendar_id: str) -> None:
    if calendar_id.startswith("shared:"):
        raise GoogleIntegrationError("Shared calendars are read-only")


def _resolve_color_id(color_id: int | None, color_name: str | None) -> str | None:
    name_color_id = COLOR_NAME_TO_ID.get(color_name) if color_name else None
    if color_id and name_color_id and color_id != name_color_id:
        raise ValueError("color_id and color_name refer to different colors")
    return str(color_id or name_color_id) if color_id or name_color_id else None


def calendar_crud(
    context: ToolContext,
    operation: str,
    calendar_id: str = "primary",
    event_id: str | None = None,
    title: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    description: str | None = None,
    location: str | None = None,
    color_id: int | None = None,
    color_name: str | None = None,
) -> str:
    if operation == "read":
        return get_event(context, calendar_id, event_id or "")
    if operation == "delete":
        return propose_event_delete(context, calendar_id, event_id or "")
    if operation == "create":
        if start is None or end is None:
            raise ValueError("start and end are required to create an event")
        return propose_event(
            context, calendar_id, title or "", start, end, description, location, color_id, color_name
        )
    return propose_event_update(
        context, calendar_id, event_id or "", title, start, end, description, location, color_id, color_name
    )


TOOLS = [
    (LIST_CALENDARS_SCHEMA, ListCalendarsParams, list_calendars),
    (LIST_EVENTS_SCHEMA, ListEventsParams, list_events),
    (CALENDAR_CRUD_SCHEMA, CalendarCrudParams, calendar_crud),
]