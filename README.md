# Perry Engine

Perry Engine is the API and agent/scheduler core. A communicator (Telegram
initially, mobile/web later) authenticates to it with `X-API-Key` and supplies
the sender identity it derived from the platform — never an identity parsed
from a message.

## Local state

Runtime state uses `DATA_DIR`, which defaults to `./data` locally and resolves
to `/data` in the Docker container. It contains:

- `db/perry.sqlite3` — short conversation memory, users, OAuth connection
  metadata, pending confirmations, and future scheduled-action definitions.
- `users/<local-id>/` — user-owned encrypted integration credentials and
  future exports. `p1` is bootstrapped as Miron.

Copy `.env.example` to `.env` and set a non-empty `ACCESS_API_KEY`. The engine
refuses to start if that key is blank.

For a temporary local communicator, run `poetry run python perry.py`. It starts
the local API when needed, chats as `p1`, and supports `/connect-calendar`.

## Google Calendar

1. In Google Cloud, create a **Web application** OAuth client, enable Google
   Calendar API, and register the exact callback URL
   `http://127.0.0.1:8000/integrations/google/calendar/callback` for the
   terminal communicator (or your real HTTPS engine URL later).
2. Fill in the four `GOOGLE_*` values in `.env`. Generate the Fernet key using
   the command in `.env.example`; do not rotate it while existing credentials
   still need to be decrypted.
3. The communicator requests `POST /integrations/google/calendar/authorize`
   with a sender identity, gives the returned URL to the user, and Google calls
   the callback after consent.

Calendar read tools can list calendars and events. Calendar writes are two
steps: the LLM can only create a short-lived proposal; the communicator must
display it and call `POST /confirmations/{confirmation_id}` after an explicit
user approval. This prevents the model from silently creating or editing an
event. Reconnect Google after enabling Tasks because the OAuth consent must
include the Tasks scope. Tasks use the same encrypted Google credentials and
follow the same confirmation rule for creates and updates.

Calendar event reads include Google `color_id` values and their resolved
foreground/background colors when available. Event proposals and updates can
set `color_id` values from Google's 1-11 event-color palette; writes still
require confirmation.

### Shared and family calendars

For Google calendars owned by friends or family, the supported no-refresh-token
option is a Google service account whose email has been explicitly granted
read-only access to each calendar. Set `GOOGLE_SERVICE_ACCOUNT_FILE` to the
credential JSON and `GOOGLE_SHARED_CALENDAR_IDS` to a comma-separated allowlist.
Service accounts cannot access arbitrary private calendars, and the integration
never writes to these shared calendars. Link a calendar to a user with
`POST /integrations/google/calendars`, passing the sender identity and:

```json
{
  "calendar_id": "friend-calendar-id",
  "display_name": "Sasha",
  "access_mode": "readonly"
}
```

The link is stored in SQLite by user, so Sasha's user account and Miron's user
account can each have different shared calendars. Use `access_mode:
"read_write"` to link multiple calendars from that user's own Google account;
the bot receives those linked names and IDs when it lists calendars.

Google Task list IDs are refreshed from the Tasks API before reading or
proposing changes. This avoids reusing stale IDs from an old conversation;
use the task-list listing tool when selecting a specific list.

Apple calendars cannot be accessed with a Google service account. Add them
through an Apple/iCloud CalDAV integration (using an app-specific password)
before treating them as availability sources; this repository currently only
implements Google OAuth and the optional Google service-account source.

<!-- todo: get the user's if registered, create accounts for them, add their personal info in the sqlite, add miron's personal info there too, make sure it is editable by the AI itself, ask miron if he knows those people and ask to provide more personal information about who that is -->
<!-- todo: startup the scheduler, make sure it is running and processing tasks, add a functionality of creating tasks from AI, use it without memory, but give needed instructions, create DB table instructions editable from the Miron's account  -->
<!-- todo: automatics : review commuting details for next week, suggest moving some events and commuting types - have a threat for that conversation -->
<!-- todo: automatics : review sport details for next week, see how much things i have to do regarding sport to upkeep my health - have a threat for that conversation -->
<!-- todo: automatics : dayli review tasks and events for tomorrow/week, find conflicts and problems, distribute, suggest corrections -->

<!-- todo: automatics : on parents requests, and dayli, review my schedyle for a given date (today, tomorrow, weekend) and respond with my plans parents are supposed to know about and distribution of when exactly i am going to be at home and when away, so they can think about food -->