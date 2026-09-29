"""HTTP client for the isolated Cognee service."""

import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.config import settings


class CogneeUnavailable(RuntimeError):
    """Raised when the Cognee service cannot be used."""


def _headers() -> dict[str, str]:
    if not settings.cognee_service_token:
        raise CogneeUnavailable("COGNEE_SERVICE_TOKEN is not configured")
    return {"Authorization": f"Bearer {settings.cognee_service_token}"}


async def index_file(user_id: str, vault: Path, path: Path) -> None:
    if not settings.cognee_enabled:
        return
    content = path.read_text(encoding="utf-8")
    payload = {
        "user_id": user_id,
        "relative_path": path.relative_to(vault).as_posix(),
        "content": content,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.cognee_service_url,
            headers=_headers(),
            timeout=settings.cognee_request_timeout_seconds,
        ) as client:
            response = await client.post("/index", json=payload)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        raise CogneeUnavailable(f"Cognee indexing request failed: {exc}") from exc


async def recall(
    user_id: str, vault: Path, query: str, max_results: int
) -> list[dict[str, str]]:
    if not settings.cognee_enabled:
        raise CogneeUnavailable("Cognee memory is disabled")
    try:
        async with httpx.AsyncClient(
            base_url=settings.cognee_service_url,
            headers=_headers(),
            timeout=settings.cognee_request_timeout_seconds,
        ) as client:
            response = await client.post(
                "/recall",
                json={"user_id": user_id, "query": query, "max_results": max_results},
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CogneeUnavailable(f"Cognee recall request failed: {exc}") from exc
    results = body.get("results")
    if not isinstance(results, list):
        raise CogneeUnavailable("Cognee returned an invalid recall response")
    return [_normalize_result(item) for item in results]


def recall_sync(user_id: str, vault: Path, query: str, max_results: int) -> list[dict[str, str]]:
    return asyncio.run(recall(user_id, vault, query, max_results))


def _normalize_result(item: Any) -> dict[str, str]:
    if isinstance(item, str):
        return {"content": item}
    if isinstance(item, dict):
        return {
            key: str(value)
            for key, value in item.items()
            if value is not None and key in {"content", "text", "dataset", "source"}
        }
    return {"content": str(item)}
