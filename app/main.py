import secrets
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from googleapiclient.errors import HttpError

from app.agent_loop import run_agent_loop
from app.config import settings
from app.integrations.google import (
    GoogleIntegrationError,
    begin_calendar_authorization,
    create_calendar_event,
    finish_calendar_authorization,
    get_calendar_service,
    get_shared_calendar_ids,
    update_calendar_event,
    get_tasks_service,
)
from app.models import ChatRequest, ChatResponse, SenderIdentity
from pydantic import BaseModel, Field
from app.storage import User, confirm_pending_action, initialize_database, resolve_user


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Perry Engine", lifespan=lifespan)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    if api_key is None or not secrets.compare_digest(api_key, settings.access_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    _: None = Depends(require_api_key),
) -> ChatResponse:
    user_id = resolve_user(request.user_id, request.user_name, request.telegram_id)
    result = run_agent_loop(user_id, request.message)
    return ChatResponse(
        reply=result.reply,
        pending_confirmations=result.pending_confirmations,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/integrations/google/calendar/authorize")
def authorize_google_calendar(
    sender: SenderIdentity,
    _: None = Depends(require_api_key),
) -> dict[str, str]:
    """Return a consent URL for the communicator to give to its user."""
    user_id = resolve_user(sender.user_id, sender.user_name, sender.telegram_id)
    try:
        return {"authorization_url": begin_calendar_authorization(user_id)}
    except GoogleIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@app.get("/integrations/google/calendar/callback")
def google_calendar_callback(request: Request, state: str, error: str | None = None) -> dict[str, str]:
    """Google redirects here; OAuth state is the one-time authorization proof."""
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Google authorization failed: {error}")
    try:
        # The app may see the proxy's internal HTTP URL even when the public
        # callback is HTTPS. OAuthlib validates the authorization response
        # scheme, so rebuild it from the configured canonical redirect URI.
        authorization_response = (
            f"{settings.google_oauth_redirect_uri}?{urlencode(request.query_params.multi_items())}"
        )
        user_id = finish_calendar_authorization(authorization_response, state)
    except GoogleIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        if exc.resp.status == 404:
            detail = (
                "The calendar event or task no longer exists. "
                "Refresh the calendar and create a new proposal."
            )
        else:
            detail = f"Google rejected the confirmed action: {exc}"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    return {"status": "connected", "user_id": user_id}


class CalendarLinkRequest(BaseModel):
    calendar_id: str = Field(min_length=1, max_length=500)
    display_name: str = Field(min_length=1, max_length=250)
    access_mode: str = Field(pattern="^(read_write|readonly)$")


@app.post("/integrations/google/calendars")
def link_google_calendar(
    link: CalendarLinkRequest,
    sender: SenderIdentity,
    _: None = Depends(require_api_key),
) -> dict[str, str]:
    """Link a visible Google calendar to a user for stable LLM selection."""
    user_id = resolve_user(sender.user_id, sender.user_name, sender.telegram_id)
    try:
        if link.access_mode == "readonly":
            if link.calendar_id.startswith("shared:"):
                raise GoogleIntegrationError("Use the raw Google calendar ID for a shared calendar")
            if link.calendar_id not in get_shared_calendar_ids():
                # A service-account calendar must be explicitly linked and
                # cannot be inferred from a global configuration value.
                raise GoogleIntegrationError(
                    "Read-only service-account calendars must be visible to the configured service account"
                )
            calendar_id = link.calendar_id
        else:
            calendar_id = link.calendar_id.removeprefix("shared:")
            response = (
                get_calendar_service(user_id)
                .calendarList()
                .get(calendarId=calendar_id)
                .execute()
            )
            link.display_name = str(response.get("summary") or link.display_name)
        User.get_by_id(user_id).save_calendar_link(
            provider="google",
            calendar_id=calendar_id,
            display_name=link.display_name,
            access_mode=link.access_mode,
        )
    except GoogleIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"status": "linked", "user_id": user_id, "calendar_id": calendar_id}


@app.post("/confirmations/{confirmation_id}")
def confirm_action(
    confirmation_id: str,
    sender: SenderIdentity,
    _: None = Depends(require_api_key),
) -> dict[str, str]:
    """Execute one approved external action; the LLM cannot invoke this route."""
    user_id = resolve_user(sender.user_id, sender.user_name, sender.telegram_id)
    confirmed = confirm_pending_action(confirmation_id, user_id)
    if confirmed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Confirmation is missing, expired, or already used")
    action_type, payload = confirmed
    try:
        if action_type == "calendar.create_event":
            event = create_calendar_event(user_id, payload["calendar_id"], payload["event"])
        elif action_type == "calendar.update_event":
            event = update_calendar_event(
                user_id, payload["calendar_id"], payload["event_id"], payload["changes"]
            )
        elif action_type == "calendar.delete_event":
            get_calendar_service(user_id).events().delete(
                calendarId=payload["calendar_id"], eventId=payload["event_id"]
            ).execute()
            event = {"id": payload["event_id"]}
        elif action_type == "tasks.create_task":
            task = get_tasks_service(user_id).tasks().insert(
                tasklist=payload["tasklist_id"], body=payload["task"]
            ).execute()
            event = task
        elif action_type == "tasks.update_task":
            event = get_tasks_service(user_id).tasks().patch(
                tasklist=payload["tasklist_id"],
                task=payload["task_id"],
                body=payload["changes"],
            ).execute()
        elif action_type == "tasks.delete_task":
            get_tasks_service(user_id).tasks().delete(
                tasklist=payload["tasklist_id"], task=payload["task_id"]
            ).execute()
            event = {"id": payload["task_id"]}
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported confirmation action")
    except GoogleIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HttpError as exc:
        if exc.resp.status == 404:
            detail = (
                "The calendar event or task no longer exists. "
                "Refresh the calendar and create a new proposal."
            )
        else:
            detail = f"Google rejected the confirmed action: {exc}"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    return {"status": "completed", "event_id": str(event.get("id", ""))}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
