from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    access_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Config-driven model choice — swapping models later is an env var
    # change, not a code change. Pick real values once you've benchmarked
    # a couple of candidates for German/Russian/English quality.
    default_model: str = "google/gemini-3.7-flash"
    cheap_model: str = ""

    max_working_turns: int = 8       # sliding window size
    max_profile_chars: int = 1600    # ~400 tokens, rough cap on the core profile
    max_loop_iterations: int = 6     # hard cap on tool-call round trips


settings = Settings()
