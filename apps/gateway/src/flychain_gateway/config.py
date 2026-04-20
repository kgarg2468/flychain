"""Settings for the FlyChain gateway, loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLYCHAIN_", env_file=".env", extra="ignore")

    env: str = "local"
    clickhouse_url: str = "http://flychain:flychain@localhost:8123/flychain"
    postgres_url: str = "postgresql://flychain:flychain@localhost:5432/flychain"
    ollama_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com"
    openai_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str | None = None
    otlp_endpoint: str | None = None
    default_project_id: str = "default"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
