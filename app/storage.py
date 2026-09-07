"""Small, dependency-free persistence layer for the single-node engine.

SQLite holds short-lived conversation context and engine metadata. User-owned
files (future encrypted OAuth credentials and exports) live under
``/data/users/<local-id>``; database files live under ``/data/db``.
"""

import re
import sqlite3
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
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


def user_data_path(local_id: str) -> Path:
    """Return a validated per-user directory without permitting path traversal."""
    local_id = _validate_local_id(local_id)
    path = settings.data_dir / "users" / local_id
    path.mkdir(parents=True, exist_ok=True)
    return path


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
            """
        )
        # Lightweight forward migration for databases created before PKCE
        # verifier persistence was added.
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
    user_data_path("p1")


def resolve_user(
    user_id: str | None,
    user_name: str | None,
    telegram_id: int | None,
) -> str:
    """Resolve communicator sender fields to one local user identity.

    A new account needs a stable local ID or Telegram ID. Names alone only
    resolve existing users so that a typo cannot silently create an account.
    """
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
    user_data_path(local_id)
    return local_id


def get_user_name(user_id: str) -> str:
    with connection() as db:
        row = db.execute("SELECT display_name FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown user")
    return str(row["display_name"])


def get_core_profile(user_id: str) -> str:
    with connection() as db:
        row = db.execute("SELECT core_profile FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown user")
    return str(row["core_profile"])[: settings.max_profile_chars]


def get_working_memory(user_id: str) -> list[Turn]:
    with connection() as db:
        rows = db.execute(
            "SELECT role, content FROM turns WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, settings.max_working_turns),
        ).fetchall()
    return [Turn(role=row["role"], content=row["content"]) for row in reversed(rows)]


def append_turn(user_id: str, turn: Turn) -> None:
    with connection() as db:
        db.execute(
            "INSERT INTO turns (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, turn.role, turn.content, _now()),
        )
        db.execute(
            """
            DELETE FROM turns
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM turns WHERE user_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (user_id, user_id, settings.max_working_turns),
        )


def save_oauth_state(user_id: str, state: str, code_verifier: str, expires_at: str) -> None:
    with connection() as db:
        db.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (_now(),))
        db.execute(
            "INSERT INTO oauth_states (state, user_id, code_verifier, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (state, user_id, code_verifier, expires_at, _now()),
        )


def consume_oauth_state(state: str) -> tuple[str, str] | None:
    """Atomically consume a one-time OAuth state token."""
    with connection() as db:
        row = db.execute(
            "DELETE FROM oauth_states WHERE state = ? AND expires_at > ? RETURNING user_id, code_verifier",
            (state, _now()),
        ).fetchone()
    if row is None or not row["code_verifier"]:
        return None
    return str(row["user_id"]), str(row["code_verifier"])


def save_connected_account(
    user_id: str,
    provider: str,
    provider_account_id: str,
    scopes: str,
    token_file: str,
    expires_at: str | None,
) -> None:
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
            (user_id, provider, provider_account_id, scopes, token_file, expires_at, now, now),
        )


def get_connected_account(user_id: str, provider: str) -> sqlite3.Row | None:
    with connection() as db:
        return db.execute(
            "SELECT * FROM connected_accounts WHERE user_id = ? AND provider = ? ORDER BY id LIMIT 1",
            (user_id, provider),
        ).fetchone()


def create_confirmation(user_id: str, action_type: str, payload: dict, expires_at: str) -> str:
    confirmation_id = str(uuid.uuid4())
    with connection() as db:
        db.execute(
            """
            INSERT INTO pending_confirmations
                (id, user_id, action_type, payload_json, expires_at, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (confirmation_id, user_id, action_type, json.dumps(payload), expires_at, _now()),
        )
    return confirmation_id


def confirm_pending_action(confirmation_id: str, user_id: str) -> tuple[str, dict] | None:
    """Claim a pending action once, preventing duplicate communicator taps."""
    with connection() as db:
        row = db.execute(
            """
            UPDATE pending_confirmations SET status = 'confirmed'
            WHERE id = ? AND user_id = ? AND status = 'pending' AND expires_at > ?
            RETURNING action_type, payload_json
            """,
            (confirmation_id, user_id, _now()),
        ).fetchone()
    if row is None:
        return None
    return str(row["action_type"]), json.loads(str(row["payload_json"]))
