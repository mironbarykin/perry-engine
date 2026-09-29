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
  long-term memory vaults and future exports. `p1` is bootstrapped as Miron.

### Obsidian memory vaults

Each user has an independent Obsidian-compatible vault at
`users/<local-id>/cognee/`. Perry creates `conversations/`, `daily/`,
`entities/`, `topics/`, and `inbox/` inside it. Conversation turns are
appended to a daily Markdown file, and a background scanner detects Markdown
changes and queues them in SQLite for the future Cognee indexer. Markdown is
the source of truth; the current foundation does not claim that files are
Cognee-indexed yet.

Perry exposes memory tools to the agent for searching and reading this vault.
Writing a durable personal note creates a confirmation proposal first; the
note is only written after the communicator confirms it through the existing
confirmation endpoint.

Cognee integration is disabled by default with `COGNEE_ENABLED=false`. When
enabled and installed, Perry stores Cognee's derived data below each user's
`cognee/.cognee/` directory and scopes datasets to that user's canonical ID.
The current Cognee release requires `openai<3`, while Perry uses
`openai>=3.6`; therefore Cognee is intentionally an adapter dependency rather
than a default dependency until the LLM client migration is designed and
tested. The indexing worker never reports a file as indexed while Cognee is
disabled or unavailable.

The Cognee service sets its process-level system database root to
`/data/cognee-system` before importing Cognee. Per-user dataset names and the
shared mounted `/data` volume provide the tenant boundary; do not expose the
Cognee service port publicly.

### Running Cognee as a separate container

The production and development Compose files include a separate `cognee`
service. It shares the `/data` volume with Perry, but has its own Python
environment and is reachable only on the Compose network.

Set these values in `.env` before enabling it:

```env
COGNEE_ENABLED=true
COGNEE_SERVICE_TOKEN=<long-random-internal-token>
COGNEE_OPENAI_API_KEY=<OpenAI-compatible-key>
COGNEE_LLM_MODEL=openai/gpt-4o-mini
COGNEE_LLM_ENDPOINT=
```

Then run locally with:

```text
docker compose -f docker-compose.development.yml up --build
```

The deployment script uploads `Dockerfile.cognee` and `cognee_service.py`;
the remote startup builds the Cognee image locally and waits for its health
check before starting Perry. If using OpenRouter, set
`COGNEE_LLM_ENDPOINT=https://openrouter.ai/api/v1` and use a model identifier
supported by OpenRouter.

Copy `.env.example` to `.env` and set a non-empty `ACCESS_API_KEY`. The engine
refuses to start if that key is blank.

For a temporary local communicator, run `poetry run python perry.py`. It starts
the local API when needed, chats as `p1`, and supports `/connect-calendar`.

## Google Calendar

1. In Google Cloud, create a **Web application** OAuth client, enable Google
   Calendar API, and register the exact callback URL configured in
   `GOOGLE_OAUTH_REDIRECT_URI`. For the deployed engine, this is
   `https://engine.perry.barykin.de/integrations/google/calendar/callback`.
   Localhost may use the HTTP callback shown in `.env.example` comments.
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

Calendar event reads include exactly `color_id`, `color` (the background hex
value), and `color_name`. The event color is preferred; if the event has no
color, the calendar default is used. Google exposes colors as numeric IDs and
hex values, so Perry maps IDs to the stable palette names `lavender`, `sage`,
`grape`, `flamingo`, `banana`, `tangerine`, `peacock`, `graphite`,
`blueberry`, `basil`, and `tomato` only when the returned hex matches the
palette. A custom calendar color can therefore have a numeric `color_id` but
`color_name: null`; the hex value is authoritative. Calendar CRUD
create/update operations accept `color_id` or `color_name`; the color is
always written to the event itself and writes still require confirmation.

If a proposed update targets an event that was deleted or changed before
confirmation, the confirmation endpoint returns a conflict instead of a
server error. Refresh the calendar and ask Perry to prepare a new proposal.

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

<!-- todo: save people's information in the database -> all the ones i mention, and are mentioned in my calendar, etc, connect to google contacts, and save their information to there, or to my database, make possible for miron to add info to those people, sometimes correlate them with users -->
<!-- todo: get the user's if registered, create accounts for them, add their personal info in the sqlite, add miron's personal info there too, make sure it is editable by the AI itself, ask miron if he knows those people and ask to provide more personal information about who that is -->
<!-- todo: startup the scheduler, make sure it is running and processing tasks, add a functionality of creating tasks from AI, use it without memory, but give needed instructions, create DB table instructions editable from the Miron's account  -->
<!-- todo: automatics : review commuting details for next week, suggest moving some events and commuting types - have a threat for that conversation -->
<!-- todo: automatics : review sport details for next week, see how much things i have to do regarding sport to upkeep my health - have a threat for that conversation -->
<!-- todo: automatics : dayli review tasks and events for tomorrow/week, find conflicts and problems, distribute, suggest corrections -->

<!-- todo: automatics : on parents requests, and dayli, review my schedyle for a given date (today, tomorrow, weekend) and respond with my plans parents are supposed to know about and distribution of when exactly i am going to be at home and when away, so they can think about food -->

<!-- todo: Make formed messages easily copied ;) ready for telegram too -->
<!-- todo: analyze previous games of the team (basketball-bunt) -->

<!-- todo: if it's an idea safe it in tasks with 💡, automatics: screen for ideas every once in a while, think about other possibilities and ask Miron about the ideas continuation! relate ideas to projects if possible, track projects progress, ask for clarification and push to finish those projects, check in every once in a while -->

<!-- todo: refactor all manipulations with calendar, task, etc, have one function for CRUD and one for GET -->
<!-- todo: easy edit only one or a few events without touching others... -->

<!-- todo: return proposed events, not via LLM, but from the db, with a simple parser, make it easy to edit only one maybe with cheaper model, etc. -->

<!-- todo: devide and treat the data (.csv) or messaged tables, .md, etc. as data sources and give them a way to be accessed and manipulated by the LLM. but create a summary of previous conversations. -->

<!-- todo: connect the cognee -->
<!-- todo: allow manipulation the automations and their messages (prompts) and shortcuts (/commands) via bot ;) -->
<!-- todo: message creating agent (general agent creating, agent calling agents) -->

<!-- todo: switch to no memory mode -->

<!-- todo: maps / public transport API -->

> This is a weekly automatic. Review the calendar for (dates) configure the transportation plan. Propose events, consider public transport, car (via parents) and cycling or running, choose the most convenient and quick ones. Use emojis like : 🚗🚇🚆 instead of event names, followed by locations. Example : "🚗 home → school" or "🚇home → gym". Choose either Sports (for running, cycling) or Commuting (car, public transport) calendar for proposals, do not create walking events. Keep in mind basketball games (coaching - at least 45 mins, referee - 60-30 mins). Consider public transport to take around an hour to commute to school, while bike only 30 minutes, and additional 15-20 to shower. Consider me leaving school 5-10 minutes after it ends.
> > This is a daily automatic. Review the commuting calendar for (day) and review the transportation plan, confirm it alligns with other events and propose changes if needed. Validate the public transport commuting times needed via tools. Return proposals if needed, and a quick overview for Miron.

> This is an automatic. Review (and refresh) the calendar for (dates) configure the nutritions plan. Propose events (named: breakfast, brunch, lunch, dinner, snack), consider the meals, that could be taken with and eaten on the go (also consider proper meals like this : example rice and chicken + vegie bowl, or just carbs + protein + vegie sources). Usually eaten on the go : bagel with putenbrust, bananas, ricewaffles, overnight oats. Consider adding a buffer time before sport's activities for correct digesting. Distribute the meals within 3-4 hours intervals. Additionally consider time needed for shopping and cooking, and propose events for them. If there are sport activities, prioritize digesting time before them. If the meal is supposed to be eaten at home, add 🏠 emoji after the name. Create those in Food calendar. Do not overlap meals with sports activity or commuting, unless it's a small snack.
> > This is a daily automatic. Review the food calendar for (day) and review the nutrition plan, confirm it alligns with other events and propose changes if needed. Return proposals if needed, and a quick overview for Miron.


> This is an automatic. Review the calendar for (dates) configure the sports plan. Propose events, consider the sport activities like gym (3x week, 1.5h), running (2x week, 0.5-1h), swimming (1x week, 1.5h) and cycling (commuting) alongside basketball refereeing, and the time needed for them and for showering after sport activities, and the time needed for digesting after meals. Distribute the sport evenly throughout the week. Do not add events if there are basketball activities, prioritize refereeing over gym over running over swimming.
> > This is an automatic. Review the calendar for (dates) configure the sports plan. Consider the sport activities like gym (3x week, 2h with shower & sauna), running (2x week, 0.5-1h), swimming (1x week, 1.5h) and cycling (commuting) alongside basketball refereeing, and the time needed for them and for showering after sport activities, and the time needed for digesting after meals. Analyze and give your opinion on the current sports plan, and propose changes to it, if needed, change naming and description if required. In the description, provide a summary of the session, specifically what is trained and what for (for gym mention body parts, but not exercises, for running mention distance and pace). Distribute the sport evenly throughout the week. Do not add events if there are basketball activities, prioritize refereeing over gym over running over swimming. If applying changes, mention reasons for that. When confirmed, crud events in Sports calendar.

> This is an automatic. Revew the calendar for (dates) configure spectator game plans. Find games in professional basketball leagues (BBL, ProA, ProB, 1RLH, 2RLH, WBV Pokal Herren) closest or at places where Miron is going to be during those dates, analyze teams and officials, rate those games and prepare top games to attend, consider travel time. Do not create events proposals yet. Prioritize already entered basketball events, but still propose spectating games if other events are movable.
>> For games No. (numbers) create events in calendar named "League Spectator Game (Referees)" and add all additional details to the event's description.

<!-- todo: automatic for looking at projects and defining next steps and tasks ;) -->
> This is an automatic. Review the calendar for (dates) configure coding plan. Propose events in personal calendar with yellow colour named coding, consider the time needed for coding currently active projects (see tasks list "Coding"), and their location, attempt to schedule coding session the way, that it psychologically supports motivation by completing small tasks quicker. Assign tasks's dates & time during the coding session times. Try to distribute the coding evenly throughout the week.

> This is a parent's request. Refresh all events in all calendars. Respond in Russian. Review the calendar for (dates) and respond with a summary of Miron's plans, including when he is at home and when he is away, so that they can plan food accordingly. Make it quickly understandable. 
> This is a parent's request. Refresh all events in all calendars. Respond in Russian. Review the calendar for (dates) and respond with times and events Miron needs to be driven to and taken from by car. Make it quickly understandable. Include: departure time from home, expected arriving time + time in road + kms. Clear departure and destination places, with links for google maps put into the adresses for easy click.

> This is an automatic. Review my google tasks (ones without a date or time, ones in the inbox list and once planned to be done during the dates) and compare them with my schedule for (dates). Find tasks that could be done during the time I have free, always propose times for tasks, do not create events for tasks specifically, move tasks to a logically corresponding list. Consider the time needed for each task, and it's importance and deadline, as well as location they could/should be done more efficient. If you need to gather more information about a task, ask me for it. Ignore coding tasks. Do not set dates for money/buy tasks. First gather all required, and only then make proposals. Rename tasks tp english and following event's naming rules, additionally include keywords like 'ref', 'buy', 'school', etc and name followed by ''. Ignore routines and ideas 💡.

> This is an automatic. Review my basketball referee games for the (dates) and create tasks with whatsapp messages for referee colleagues, put them 3 or 4 days in advance dependable on wether I am a SR1 or SR2. In the message itself include the location, time of tipoff, league, and when I would be there (time). Example : "Hallo Luke,\n wir pfeifen zusammen am Sonntag (20.09.) U16 Landesliga (SG ART Giants Düsseldorf vs. DJK Bad Münstereifel) in der Halle Aldekerk (Adresse) um 12:00 Uhr. Ich bin gegen 11:25 Uhr in der Halle. Habe neues Silber und Schwarz und altes Silber, was würdest du anziehen?) \n Bis dann! VG Miron ;)"

> This is an automatic. Review my basketball coach game for the (dates) and return a message in ``` with the details for players and parents, including the location, time of tipoff, against whom, and where players are supposed to be there. Finish it with VG Miron ;)"