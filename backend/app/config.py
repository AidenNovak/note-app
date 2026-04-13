from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def running_on_vercel() -> bool:
    return bool(os.getenv("VERCEL"))


def vercel_environment() -> str | None:
    return os.getenv("VERCEL_ENV")


def _runtime_root() -> Path:
    return Path("/tmp/note-app")


def _resolve_runtime_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    cleaned = raw_path[2:] if raw_path.startswith("./") else raw_path
    return _runtime_root() / cleaned


def _sqlite_url_to_path(database_url: str) -> Path | None:
    prefixes = ("sqlite+aiosqlite:///", "sqlite:///")
    for prefix in prefixes:
        if database_url.startswith(prefix):
            return Path(database_url[len(prefix):])
    return None


def _path_to_sqlite_url(path: Path, async_driver: bool) -> str:
    prefix = "sqlite+aiosqlite:///" if async_driver else "sqlite:///"
    return f"{prefix}{path.as_posix()}"


def _normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return f"postgresql+asyncpg://{database_url[len('postgres://'):]}"
    if database_url.startswith("postgresql://"):
        return f"postgresql+asyncpg://{database_url[len('postgresql://'):]}"
    if database_url.startswith("postgresql+psycopg://"):
        return f"postgresql+asyncpg://{database_url[len('postgresql+psycopg://'):]}"
    return database_url


class Settings(BaseSettings):
    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_NAME: str = "atelier API"
    APP_VERSION: str = "1.0.0"
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/notes.db"
    # SECURITY: SECRET_KEY must be set via environment variable in production
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    SECRET_KEY: str = Field(min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    EASYSTARTER_SERVER_URL: str = ""
    STORAGE_MIGRATION_TOKEN: str = ""
    STORAGE_PATH: str = "./data/files"
    MAX_FILE_SIZE_MB: int = 500
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:8082",
        "http://127.0.0.1:8082",
        "http://localhost:8083",
        "http://127.0.0.1:8083",
    ]

    # ── Unified AI Configuration ──────────────────────────
    # All LLM calls go through OpenRouter. Change AI_MODEL to swap globally.
    # Browse models: https://openrouter.ai/models
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    AI_MODEL: str = "moonshotai/kimi-k2.5"
    AI_MAX_TOKENS: int = 4096
    AI_TEMPERATURE: float = 0.7
    AI_STREAMING: bool = True

    # Provider-specific keys (only for specialized tasks, NOT for general LLM)
    OPENAI_API_KEY: str = ""       # Whisper audio transcription
    ANTHROPIC_API_KEY: str = ""    # Claude Vision image description

    # Embedding (独立于语言模型)
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"

    INSIGHTS_WORKSPACE_ROOT: str = "./data/insights"
    INSIGHT_MAX_CONTEXT_NOTES: int = 12
    INSIGHT_MAX_NOTE_CHARS: int = 4000
    INSIGHT_AGENT_MAX_TURNS: int = 30

    # Workspace Agent
    AGENT_MAX_TURNS: int = 25
    AGENT_MAX_TOKENS_PER_TURN: int = 4096
    AGENT_REQUEST_TIMEOUT: int = 180
    AGENT_MAX_NOTES: int = 50

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        # Validate SECRET_KEY
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set via environment variable. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )

        exact_weak = {"change-me-in-production", "test", "dev", "secret"}
        if self.SECRET_KEY in exact_weak:
            raise ValueError(
                "SECRET_KEY is too weak. Use a cryptographically secure random string. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )

        if self.SECRET_KEY.startswith("change-me-in-production"):
            raise ValueError(
                "SECRET_KEY is too weak. Use a cryptographically secure random string. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )

        for prefix in ("test", "dev", "secret"):
            if self.SECRET_KEY.startswith(prefix):
                suffix = self.SECRET_KEY[len(prefix):]
                if suffix and set(suffix) == {"x"}:
                    raise ValueError(
                        "SECRET_KEY is too weak. Use a cryptographically secure random string. "
                        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                    )

        # Auto-configure database for Vercel
        if running_on_vercel() and self.DATABASE_URL == "sqlite+aiosqlite:///./data/notes.db":
            self.DATABASE_URL = (
                os.getenv("POSTGRES_URL_NON_POOLING")
                or os.getenv("POSTGRES_URL")
                or os.getenv("DATABASE_URL")
                or self.DATABASE_URL
            )

        self.DATABASE_URL = _normalize_async_database_url(self.DATABASE_URL)

        if running_on_vercel():
            storage_path = _resolve_runtime_path(self.STORAGE_PATH)
            insights_workspace = _resolve_runtime_path(self.INSIGHTS_WORKSPACE_ROOT)
            database_path = _sqlite_url_to_path(self.DATABASE_URL)

            self.STORAGE_PATH = str(storage_path)
            self.INSIGHTS_WORKSPACE_ROOT = str(insights_workspace)

            if database_path is not None and not database_path.is_absolute():
                self.DATABASE_URL = _path_to_sqlite_url(
                    _resolve_runtime_path(database_path.as_posix()),
                    async_driver=self.DATABASE_URL.startswith("sqlite+aiosqlite:///"),
                )

        if self.APP_ENV == "production" and self.SECRET_KEY == "change-me-in-production":
            raise ValueError("SECRET_KEY must be changed in production")
        return self


settings = Settings()

Path(settings.STORAGE_PATH).mkdir(parents=True, exist_ok=True)
Path(settings.INSIGHTS_WORKSPACE_ROOT).mkdir(parents=True, exist_ok=True)

database_path = _sqlite_url_to_path(settings.DATABASE_URL)
if database_path is not None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
