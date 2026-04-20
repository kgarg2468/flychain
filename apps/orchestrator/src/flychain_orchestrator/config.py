"""Settings for the FlyChain orchestrator."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLYCHAIN_", env_file=".env", extra="ignore")

    env: str = "local"
    clickhouse_url: str = "http://flychain:flychain@localhost:8123/flychain"
    postgres_url: str = "postgresql://flychain:flychain@localhost:5432/flychain"
    redis_url: str = "redis://localhost:6379/0"
    ollama_url: str = "http://localhost:11434"
    gateway_url: str = "http://localhost:8080"
    judge_model: str = "llama3.2:3b-instruct"
    embedding_model: str = "nomic-embed-text"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
