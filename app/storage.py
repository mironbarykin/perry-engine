"""Small, dependency-free persistence layer for the single-node engine.

SQLite holds short-lived conversation context and engine metadata. User-owned
files live under ``/data/users/<local-id>``; database files live under ``/data/db``.
"""

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.models import Turn

LOCAL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

MIRON_PROFILE = (
    "Born on 24th of April 2007 in Moscow, Russia. Lived for 15 years in "
    "Khimki, Russia. Moved to Düsseldorf, Germany in 2022, lives in Meerbusch, "
    "Germany. Speaks Russian, English, German. Has a strong interest in "
    "basketball, programming, finance and self-improvement. Came out as gay "
    "in 2023. Currently studying in Theodor-Flidner-Gymnasium in Düsseldorf "
    "and finishes in 2027 with Abitur. Plans to study Computer Science at "
    "university in Europe. Plays basketball since 2014, referees and coaches "
    "since 2023, and dreams of becoming a EuroLeague basketball referee."
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_local_id(local_id: str) -> str:
    local_id = local_id.strip().lower()
    if not LOCAL_ID_RE.fullmatch(local_id):
        raise ValueError("user_id must start with a letter and contain only a-z, 0-9, _ or -")
    return local_id


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize_database() -> None:
    """Create durable engine state and bootstrap Perry's primary account."""
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                telegram_id INTEGER UNIQUE,
                core_profile TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS turns_user_id_id ON turns(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS connected_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                provider_account_id TEXT NOT NULL,
                scopes TEXT NOT NULL DEFAULT '',
                token_file TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, provider, provider_account_id)
            );
            CREATE TABLE IF NOT EXISTS calendar_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                calendar_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                access_mode TEXT NOT NULL CHECK(access_mode IN ('read_write', 'readonly')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, provider, calendar_id)
            );
            CREATE TABLE IF NOT EXISTS pending_confirmations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'confirmed', 'cancelled', 'expired')),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scheduled_actions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                action_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                timezone TEXT NOT NULL,
                schedule_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                code_verifier TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS basketball_leagues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                liga_id INTEGER UNIQUE NOT NULL,
                verband_id INTEGER NOT NULL DEFAULT 11,
                klasse TEXT NOT NULL DEFAULT '',
                altersklasse TEXT NOT NULL DEFAULT '',
                geschlecht TEXT NOT NULL DEFAULT '',
                bezirk TEXT NOT NULL DEFAULT '',
                kreis TEXT NOT NULL DEFAULT '',
                liganame TEXT NOT NULL,
                liganr TEXT NOT NULL DEFAULT '',
                short_name TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_bball_leagues_verband ON basketball_leagues(verband_id);
            CREATE INDEX IF NOT EXISTS idx_bball_leagues_short_name ON basketball_leagues(short_name);
            CREATE INDEX IF NOT EXISTS idx_bball_leagues_liganr ON basketball_leagues(liganr);

            CREATE TABLE IF NOT EXISTS basketball_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                liga_id INTEGER NOT NULL REFERENCES basketball_leagues(liga_id) ON DELETE CASCADE,
                spiel_nr INTEGER NOT NULL,
                spieltag INTEGER NOT NULL,
                datum TEXT NOT NULL,
                heim TEXT NOT NULL,
                gast TEXT NOT NULL,
                spielhalle TEXT NOT NULL DEFAULT '',
                schiedsrichter TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(liga_id, spiel_nr)
            );
            CREATE INDEX IF NOT EXISTS idx_bball_games_liga_datum ON basketball_games(liga_id, datum);

            CREATE TABLE IF NOT EXISTS basketball_league_sync (
                liga_id INTEGER PRIMARY KEY,
                last_synced_at TEXT NOT NULL
            );
            """
        )
        oauth_state_columns = {row["name"] for row in db.execute("PRAGMA table_info(oauth_states)")}
        if "code_verifier" not in oauth_state_columns:
            db.execute("ALTER TABLE oauth_states ADD COLUMN code_verifier TEXT")
            
        now = _now()
        db.execute(
            """
            INSERT INTO users (id, display_name, core_profile, created_at, updated_at)
            VALUES ('p1', 'Miron', ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (MIRON_PROFILE, now, now),
        )
    User.get_by_id("p1")  # Ensure user directory exists


class User:
    def __init__(
        self,
        id: str,
        display_name: str | None = None,
        telegram_id: int | None = None,
        core_profile: str = "",
    ):
        self.id = _validate_local_id(id)
        self.display_name = display_name
        self.telegram_id = telegram_id
        self.core_profile = core_profile

    @property
    def data_path(self) -> Path:
        """Return a validated per-user directory without permitting path traversal."""
        path = settings.data_dir / "users" / self.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_by_id(cls, user_id: str) -> "User":
        user_id = _validate_local_id(user_id)
        with connection() as db:
            row = db.execute(
                "SELECT id, display_name, telegram_id, core_profile FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Unknown user: {user_id}")
        return cls(
            id=row["id"],
            display_name=row["display_name"],
            telegram_id=row["telegram_id"],
            core_profile=row["core_profile"],
        )

    @classmethod
    def resolve(
        cls,
        user_id: str | None = None,
        user_name: str | None = None,
        telegram_id: int | None = None,
    ) -> "User":
        """Resolve communicator sender fields to one local User instance."""
        initialize_database()
        supplied_id = _validate_local_id(user_id) if user_id else None
        supplied_name = user_name.strip() if user_name else None

        with connection() as db:
            clauses: list[str] = []
            parameters: list[object] = []
            if supplied_id:
                clauses.append("id = ?")
                parameters.append(supplied_id)
            if telegram_id:
                clauses.append("telegram_id = ?")
                parameters.append(telegram_id)
            if supplied_name:
                clauses.append("lower(display_name) = lower(?)")
                parameters.append(supplied_name)

            rows = db.execute(
                f"SELECT id FROM users WHERE {' OR '.join(clauses)}", parameters
            ).fetchall()
            matches = {row["id"] for row in rows}

            if len(matches) > 1:
                raise ValueError("sender identifiers belong to different users")

            if matches:
                local_id = matches.pop()
                if telegram_id:
                    db.execute(
                        "UPDATE users SET telegram_id = ?, updated_at = ? WHERE id = ?",
                        (telegram_id, _now(), local_id),
                    )
            else:
                local_id = supplied_id or (f"tg-{telegram_id}" if telegram_id else "")
                if not local_id:
                    raise ValueError("Unknown user name; provide user_id or telegram_id")
                db.execute(
                    "INSERT INTO users (id, display_name, telegram_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (local_id, supplied_name or local_id, telegram_id, _now(), _now()),
                )

        return cls.get_by_id(local_id)

    def save(self) -> None:
        """Persist or update core profile data."""
        now = _now()
        with connection() as db:
            db.execute(
                """
                INSERT INTO users (id, display_name, telegram_id, core_profile, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    telegram_id = excluded.telegram_id,
                    core_profile = excluded.core_profile,
                    updated_at = excluded.updated_at
                """,
                (self.id, self.display_name, self.telegram_id, self.core_profile, now, now),
            )

    def get_working_memory(self) -> list[Turn]:
        """Return the most recent conversation turns for this user, in chronological order."""
        cutoff = (datetime.now(UTC) - timedelta(days=settings.working_memory_max_age_days)).isoformat()
        with connection() as db:
            rows = db.execute(
                """
                SELECT role, content FROM turns
                WHERE user_id = ? AND created_at > ?
                ORDER BY id DESC LIMIT ?
                """,
                (self.id, cutoff, settings.max_working_turns),
            ).fetchall()
        return [Turn(role=row["role"], content=row["content"]) for row in reversed(rows)]

    def append_turn(self, turn: Turn) -> None:
        """Append a new conversation turn for this user, trimming old turns if needed."""
        with connection() as db:
            db.execute(
                "INSERT INTO turns (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (self.id, turn.role, turn.content, _now()),
            )
            db.execute(
                """
                DELETE FROM turns
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM turns WHERE user_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (self.id, self.id, settings.max_working_turns),
            )

    def save_oauth_state(self, state: str, code_verifier: str, expires_at: str) -> None:
        with connection() as db:
            db.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (_now(),))
            db.execute(
                "INSERT INTO oauth_states (state, user_id, code_verifier, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (state, self.id, code_verifier, expires_at, _now()),
            )

    def save_connected_account(
        self,
        provider: str,
        provider_account_id: str,
        scopes: str,
        token_file: str,
        expires_at: str | None,
    ) -> None:
        """Persist a connected account for the user, creating or updating as needed."""
        now = _now()
        with connection() as db:
            db.execute(
                """
                INSERT INTO connected_accounts
                    (user_id, provider, provider_account_id, scopes, token_file, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider, provider_account_id) DO UPDATE SET
                    scopes = excluded.scopes, token_file = excluded.token_file,
                    expires_at = excluded.expires_at, updated_at = excluded.updated_at
                """,
                (self.id, provider, provider_account_id, scopes, token_file, expires_at, now, now),
            )

    def get_connected_account(self, provider: str) -> sqlite3.Row | None:
        """Return the first connected account for a given provider, or None if none exists."""
        with connection() as db:
            return db.execute(
                "SELECT * FROM connected_accounts WHERE user_id = ? AND provider = ? ORDER BY id LIMIT 1",
                (self.id, provider),
            ).fetchone()

    def save_calendar_link(
        self,
        provider: str,
        calendar_id: str,
        display_name: str,
        access_mode: str,
    ) -> None:
        if access_mode not in {"read_write", "readonly"}:
            raise ValueError("Unsupported calendar access mode")
        now = _now()
        with connection() as db:
            db.execute(
                """
                INSERT INTO calendar_links
                    (user_id, provider, calendar_id, display_name, access_mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider, calendar_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    access_mode = excluded.access_mode,
                    updated_at = excluded.updated_at
                """,
                (self.id, provider, calendar_id, display_name, access_mode, now, now),
            )

    def get_calendar_links(self, provider: str = "google") -> list[sqlite3.Row]:
        with connection() as db:
            return db.execute(
                """
                SELECT * FROM calendar_links
                WHERE user_id = ? AND provider = ?
                ORDER BY display_name, calendar_id
                """,
                (self.id, provider),
            ).fetchall()

    def create_confirmation(self, action_type: str, payload: dict, expires_at: str) -> str:
        """ Create a pending confirmation for a user action, returning a unique confirmation ID. """
        confirmation_id = str(uuid.uuid4())
        with connection() as db:
            db.execute(
                """
                INSERT INTO pending_confirmations
                    (id, user_id, action_type, payload_json, expires_at, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (confirmation_id, self.id, action_type, json.dumps(payload), expires_at, _now()),
            )
        return confirmation_id

    def confirm_pending_action(self, confirmation_id: str) -> tuple[str, dict] | None:
        """ Atomically confirm a pending action and return its type and payload, or None if invalid/expired. """
        with connection() as db:
            row = db.execute(
                """
                UPDATE pending_confirmations SET status = 'confirmed'
                WHERE id = ? AND user_id = ? AND status = 'pending' AND expires_at > ?
                RETURNING action_type, payload_json
                """,
                (confirmation_id, self.id, _now()),
            ).fetchone()
        if row is None:
            return None
        return str(row["action_type"]), json.loads(str(row["payload_json"]))


def resolve_user(
    user_id: str | None = None,
    user_name: str | None = None,
    telegram_id: int | None = None,
) -> str:
    """Resolve communicator identity and return the local user ID."""
    return User.resolve(user_id, user_name, telegram_id).id


def get_user_name(user_id: str) -> str:
    """Return the display name used in the agent prompt."""
    user = User.get_by_id(user_id)
    return user.display_name or user.id


def get_core_profile(user_id: str) -> str:
    return User.get_by_id(user_id).core_profile


def get_working_memory(user_id: str) -> list[Turn]:
    return User.get_by_id(user_id).get_working_memory()


def append_turn(user_id: str, turn: Turn) -> None:
    User.get_by_id(user_id).append_turn(turn)


def confirm_pending_action(
    confirmation_id: str,
    user_id: str,
) -> tuple[str, dict] | None:
    return User.get_by_id(user_id).confirm_pending_action(confirmation_id)
    
def consume_oauth_state(state: str) -> tuple[str, str] | None:
    """Atomically consume a one-time OAuth state token without needing an existing User instance."""
    with connection() as db:
        row = db.execute(
            "DELETE FROM oauth_states WHERE state = ? AND expires_at > ? RETURNING user_id, code_verifier",
            (state, _now()),
        ).fetchone()
    if row is None or not row["code_verifier"]:
        return None
    return str(row["user_id"]), str(row["code_verifier"])