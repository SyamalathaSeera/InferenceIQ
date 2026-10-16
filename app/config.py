from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from environment variables or a local .env file."""

    provider: Literal["mock", "openai", "anthropic", "ollama"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_input_cost_per_million: float | None = None
    openai_output_cost_per_million: float | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_input_cost_per_million: float | None = None
    anthropic_output_cost_per_million: float | None = None
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    database_url: str = "sqlite+aiosqlite:///./inferenceiq.db"
    auto_create_schema: bool = True
    inferenceiq_langsmith_enabled: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "inferenceiq"
    langgraph_checkpoint_url: str = "./inferenceiq-checkpoints.sqlite"
    inferenceiq_auth_required: bool = False
    inferenceiq_admin_token: str = "dev-admin-key"
    inferenceiq_audit_signing_key: str = "dev-audit-signing-key"
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: int = 30
    health_ewma_alpha: float = 0.2
    dashboard_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
