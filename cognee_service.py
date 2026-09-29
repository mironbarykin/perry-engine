import asyncio
import os
from typing import Any

os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", "/data/cognee-system")
os.environ.setdefault("DATA_ROOT_DIRECTORY", "/data")

import cognee
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

SERVICE_TOKEN = os.environ["COGNEE_SERVICE_TOKEN"]
DATA_ROOT = os.environ["DATA_ROOT_DIRECTORY"]
LOCK = asyncio.Lock()


class IndexRequest(BaseModel):
    user_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    relative_path: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=1_000_000)


class RecallRequest(BaseModel):
    user_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=20)


app = FastAPI(title="Perry Cognee Service")


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )

def dataset_name(user_id: str) -> str:
    return f"perry_user_{user_id}"


def configure_user(user_id: str) -> None:
    root = os.path.join(DATA_ROOT, "users", user_id, "cognee", ".cognee")
    os.makedirs(os.path.join(root, "system"), exist_ok=True)
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    cognee.config.system_root_directory(os.path.join(root, "system"))
    cognee.config.data_root_directory(os.path.join(root, "data"))
    api_key = os.getenv("COGNEE_OPENAI_API_KEY", "")
    if api_key:
        cognee.config.set_llm_provider("openai")
        cognee.config.set_llm_api_key(api_key)
        cognee.config.set_llm_model(
            os.getenv("COGNEE_LLM_MODEL", "openai/gpt-4o-mini")
        )
    endpoint = os.getenv("COGNEE_LLM_ENDPOINT", "")
    if endpoint:
        cognee.config.set_llm_endpoint(endpoint)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/index", dependencies=[Depends(require_token)])
async def index(request: IndexRequest) -> dict[str, str]:
    async with LOCK:
        configure_user(request.user_id)
        await cognee.remember(
            request.content,
            dataset_name=dataset_name(request.user_id),
            self_improvement=False,
            raise_on_error=True,
        )
    return {"status": "indexed", "relative_path": request.relative_path}


@app.post("/recall", dependencies=[Depends(require_token)])
async def recall(request: RecallRequest) -> dict[str, Any]:
    async with LOCK:
        configure_user(request.user_id)
        results = await cognee.recall(
            request.query,
            datasets=[dataset_name(request.user_id)],
            top_k=request.max_results,
        )
    return {
        "results": [
            result if isinstance(result, (str, dict)) else str(result)
            for result in results
        ]
    }
