from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    access_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = Field(default=60.0, gt=0)

    # Config-driven model choice — swapping models later is an env var
    # change, not a code change. Pick real values once you've benchmarked
    # a couple of candidates for German/Russian/English quality.
    default_model: str = "google/gemini-3.7-flash"
    cheap_model: str = ""

    # Google OAuth is intentionally optional until the Calendar integration is
    # connected. These secrets belong in the deployment environment, never in
    # the repository or an LLM message.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    google_token_encryption_key: str = ""
    google_oauth_timeout_seconds: float = Field(default=30.0, gt=0)
    google_api_timeout_seconds: float = Field(default=30.0, gt=0)
    # Optional service account used only for calendars explicitly shared with
    # that service-account identity. It cannot access arbitrary calendars.
    google_service_account_file: Path | None = None

    max_working_turns: int = 8       # sliding window size
    working_memory_max_age_days: int = Field(default=3, ge=1)
    max_profile_chars: int = 1600    # ~400 tokens, rough cap on the core profile
    # Calendar planning can need discovery calls plus one proposal per event.
    # Keep this bounded, but allow a normal multi-event planning request to
    # reach a final answer after its confirmations have been prepared.
    max_loop_iterations: int = Field(default=12, ge=1)

    # Everything that must survive a container restart lives on the mounted
    # volume. Keep application code and secrets out of this directory.
    # Docker runs from /, so this resolves to the mounted /data volume there;
    # locally it resolves to ./data in the project.
    data_dir: Path = Path("data")

    @field_validator("access_api_key")
    @classmethod
    def access_api_key_must_not_be_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ACCESS_API_KEY must be set and non-empty")
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "db" / "perry.sqlite3"


settings = Settings()
