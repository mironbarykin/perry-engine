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
event.
