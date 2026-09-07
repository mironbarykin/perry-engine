"""User-scoped Google OAuth and Calendar access."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import httplib2
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from app.config import settings
from app.storage import (
    consume_oauth_state,
    get_connected_account,
    save_connected_account,
    save_oauth_state,
    user_data_path,
)

GOOGLE_PROVIDER = "google"
CALENDAR_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
]
TOKEN_FILENAME = "google/calendar-credentials.fernet"


class GoogleIntegrationError(RuntimeError):
    pass


def _require_oauth_configuration() -> None:
    if not all(
        (
            settings.google_oauth_client_id,
            settings.google_oauth_client_secret,
            settings.google_oauth_redirect_uri,
            settings.google_token_encryption_key,
        )
    ):
        raise GoogleIntegrationError(
            "Google Calendar is not configured. Set GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REDIRECT_URI and "
            "GOOGLE_TOKEN_ENCRYPTION_KEY."
        )
    redirect = urlparse(settings.google_oauth_redirect_uri)
    loopback_hosts = {"localhost", "127.0.0.1", "::1"}
    if redirect.scheme != "https" and redirect.hostname not in loopback_hosts:
        raise GoogleIntegrationError("Google OAuth redirect URI must use HTTPS outside localhost")


def _allow_loopback_http_oauth() -> None:
    """OAuthlib requires an opt-in for HTTP; Google permits loopback callbacks."""
    redirect = urlparse(settings.google_oauth_redirect_uri)
    if redirect.scheme == "http" and redirect.hostname in {"localhost", "127.0.0.1", "::1"}:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _fernet() -> Fernet:
    _require_oauth_configuration()
    try:
        return Fernet(settings.google_token_encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise GoogleIntegrationError("GOOGLE_TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from exc


def _flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    _require_oauth_configuration()
    flow_kwargs: dict[str, object] = {
        "state": state,
        "redirect_uri": settings.google_oauth_redirect_uri,
    }
    if code_verifier:
        flow_kwargs["code_verifier"] = code_verifier
        flow_kwargs["autogenerate_code_verifier"] = False
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_oauth_redirect_uri],
            }
        },
        scopes=CALENDAR_SCOPES,
        **flow_kwargs,
    )


def begin_calendar_authorization(user_id: str) -> str:
    state = secrets.token_urlsafe(32)
    expires_at = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    flow = _flow(state)
    authorization_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    if not flow.code_verifier:
        raise GoogleIntegrationError("Could not create the Google OAuth PKCE verifier")
    save_oauth_state(user_id, state, flow.code_verifier, expires_at)
    return authorization_url


def _token_path(user_id: str) -> Path:
    path = user_data_path(user_id) / TOKEN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _save_credentials(user_id: str, credentials: Credentials, account_id: str) -> None:
    token_path = _token_path(user_id)
    token_path.write_bytes(_fernet().encrypt(credentials.to_json().encode()))
    save_connected_account(
        user_id=user_id,
        provider=GOOGLE_PROVIDER,
        provider_account_id=account_id,
        scopes=" ".join(credentials.scopes or CALENDAR_SCOPES),
        token_file=TOKEN_FILENAME,
        expires_at=credentials.expiry.isoformat() if credentials.expiry else None,
    )


def finish_calendar_authorization(authorization_response: str, state: str) -> str:
    authorization = consume_oauth_state(state)
    if authorization is None:
        raise GoogleIntegrationError("OAuth state is invalid or expired")
    user_id, code_verifier = authorization
    flow = _flow(state, code_verifier)
    _allow_loopback_http_oauth()
    try:
        flow.fetch_token(
            authorization_response=authorization_response,
            timeout=settings.google_oauth_timeout_seconds,
        )
    except OAuth2Error as exc:
        raise GoogleIntegrationError(f"Google token exchange failed: {exc.description}") from exc
    credentials = flow.credentials
    userinfo = (
        build(
            "oauth2",
            "v2",
            credentials=credentials,
            http=httplib2.Http(timeout=settings.google_api_timeout_seconds),
            cache_discovery=False,
        )
        .userinfo()
        .get()
        .execute()
    )
    account_id = str(userinfo.get("email") or userinfo.get("id"))
    if not account_id:
        raise GoogleIntegrationError("Google did not return an account identity")
    _save_credentials(user_id, credentials, account_id)
    return user_id


def get_calendar_service(user_id: str):
    account = get_connected_account(user_id, GOOGLE_PROVIDER)
    if account is None:
        raise GoogleIntegrationError("Google Calendar is not connected for this user")
    token_path = user_data_path(user_id) / str(account["token_file"])
    try:
        credentials = Credentials.from_authorized_user_info(
            json.loads(_fernet().decrypt(token_path.read_bytes()).decode())
        )
    except (FileNotFoundError, InvalidToken, json.JSONDecodeError, ValueError) as exc:
        raise GoogleIntegrationError("Stored Google credentials are unavailable or invalid") from exc
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_credentials(user_id, credentials, str(account["provider_account_id"]))
    if not credentials.valid:
        raise GoogleIntegrationError("Google authorization has expired; reconnect Calendar")
    return build(
        "calendar",
        "v3",
        credentials=credentials,
        http=httplib2.Http(timeout=settings.google_api_timeout_seconds),
        cache_discovery=False,
    )


def create_calendar_event(user_id: str, calendar_id: str, event: dict) -> dict:
    return get_calendar_service(user_id).events().insert(calendarId=calendar_id, body=event).execute()


def update_calendar_event(user_id: str, calendar_id: str, event_id: str, changes: dict) -> dict:
    return (
        get_calendar_service(user_id)
        .events()
        .patch(calendarId=calendar_id, eventId=event_id, body=changes)
        .execute()
    )
