"""Per-user Markdown vault and asynchronous indexing queue foundation."""

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.cognee_adapter import index_file
from app.models import Turn
from app.storage import User

logger = logging.getLogger(__name__)

VAULT_DIRNAME = "cognee"
TRANSCRIPT_DIRNAME = "conversations"


def vault_path(user_id: str) -> Path:
    """Return and initialize the user's isolated Obsidian-compatible vault."""
    vault = User.get_by_id(user_id).data_path / VAULT_DIRNAME
    (vault / TRANSCRIPT_DIRNAME).mkdir(parents=True, exist_ok=True)
    for directory in ("daily", "entities", "topics", "inbox"):
        (vault / directory).mkdir(exist_ok=True)
    return vault


def _transcript_path(vault: Path, timestamp: datetime) -> Path:
    return vault / TRANSCRIPT_DIRNAME / f"{timestamp:%Y-%m-%d}.md"


def append_transcript(user_id: str, turn: Turn) -> Path:
    """Append a turn to the user's daily Markdown transcript and queue it."""
    timestamp = datetime.now(UTC)
    vault = vault_path(user_id)
    path = _transcript_path(vault, timestamp)
    if not path.exists():
        path.write_text(
            f"---\nuser_id: {user_id}\nsource: conversation\ncreated_at: {timestamp.isoformat()}\n---\n\n",
            encoding="utf-8",
        )
    with path.open("a", encoding="utf-8") as file:
        file.write(f"## {timestamp.isoformat()} — {turn.role}\n\n{turn.content}\n\n")
    _queue_file(user_id, vault, path)
    return path


def _queue_file(user_id: str, vault: Path, path: Path) -> None:
    content = path.read_bytes()
    relative_path = path.relative_to(vault).as_posix()
    User.get_by_id(user_id).queue_memory_job(
        relative_path=relative_path,
        content_hash=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        modified_at_ns=path.stat().st_mtime_ns,
    )


def _safe_note_path(user_id: str, relative_path: str) -> tuple[Path, Path]:
    vault = vault_path(user_id).resolve()
    candidate = (vault / relative_path).resolve()
    if vault not in candidate.parents and candidate.parent != vault:
        raise ValueError("memory note path escapes the user's vault")
    if candidate.suffix.lower() != ".md":
        raise ValueError("memory note path must end with .md")
    return vault, candidate


def read_note(user_id: str, relative_path: str) -> str:
    _, path = _safe_note_path(user_id, relative_path)
    if not path.is_file():
        raise ValueError(f"Memory note does not exist: {relative_path}")
    return path.read_text(encoding="utf-8")


def search_notes(user_id: str, query: str, max_results: int) -> list[dict[str, str]]:
    vault = vault_path(user_id)
    terms = [term.casefold() for term in query.split() if term.strip()]
    results: list[dict[str, str]] = []
    for path in sorted(vault.rglob("*.md")):
        content = path.read_text(encoding="utf-8")
        if all(term in content.casefold() for term in terms):
            results.append(
                {
                    "path": path.relative_to(vault).as_posix(),
                    "snippet": content[:1000],
                }
            )
            if len(results) >= max_results:
                break
    return results


def write_note(user_id: str, relative_path: str, content: str) -> Path:
    vault, path = _safe_note_path(user_id, relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    _queue_file(user_id, vault, path)
    return path


def queue_note_for_index(user_id: str, relative_path: str) -> None:
    vault, path = _safe_note_path(user_id, relative_path)
    if not path.is_file():
        raise ValueError(f"Memory note does not exist: {relative_path}")
    _queue_file(user_id, vault, path)


def scan_user_vault(user_id: str) -> int:
    """Queue changed Markdown files in one user's vault, returning the count."""
    vault = vault_path(user_id)
    queued = 0
    for path in vault.rglob("*.md"):
        if path.is_file():
            _queue_file(user_id, vault, path)
            queued += 1
    return queued


def scan_all_user_vaults() -> int:
    """Scan all registered users; indexing is intentionally handled later."""
    from app.storage import connection

    with connection() as db:
        user_ids = [str(row["id"]) for row in db.execute("SELECT id FROM users")]
    return sum(scan_user_vault(user_id) for user_id in user_ids)


async def run_memory_sync_scanner(stop_event: asyncio.Event) -> None:
    """Periodically discover Obsidian edits without blocking request handling."""
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(scan_all_user_vaults)
        except Exception:
            logger.exception("Memory vault scan failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.memory_sync_interval_seconds
            )
        except asyncio.TimeoutError:
            continue


async def run_memory_index_worker(stop_event: asyncio.Event) -> None:
    """Process queued files when Cognee is enabled, retaining failures visibly."""
    while not stop_event.is_set():
        if not settings.cognee_enabled:
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.memory_sync_interval_seconds
                )
            except asyncio.TimeoutError:
                continue
            continue
        job = await asyncio.to_thread(User.claim_memory_job)
        if job is not None:
            user_id = str(job["user_id"])
            relative_path = str(job["relative_path"])
            try:
                vault, path = _safe_note_path(user_id, relative_path)
                if not path.is_file():
                    raise ValueError(f"Queued memory note does not exist: {relative_path}")
                await index_file(user_id, vault, path)
                await asyncio.to_thread(User.mark_memory_document_indexed, user_id, relative_path)
                await asyncio.to_thread(User.complete_memory_job, int(job["id"]))
            except Exception as exc:
                logger.exception("Memory indexing failed for %s/%s", user_id, relative_path)
                await asyncio.to_thread(
                    User.mark_memory_document_failed, user_id, relative_path, str(exc)
                )
                await asyncio.to_thread(User.fail_memory_job, int(job["id"]), str(exc))
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.memory_sync_interval_seconds)
        except asyncio.TimeoutError:
            continue
