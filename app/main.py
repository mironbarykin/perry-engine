import secrets

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.agent_loop import run_agent_loop
from app.config import settings
from app.models import ChatRequest, ChatResponse

app = FastAPI(title="Perry Engine")
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
    reply = run_agent_loop(request.user_id, request.message)
    return ChatResponse(reply=reply)


@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)