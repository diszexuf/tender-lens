"""Настройки приложения из переменных окружения и файла .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # .env в корне репозитория (локальный запуск из backend/) или рядом
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Приложение
    app_env: Literal["dev", "prod", "test"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "tender_lens"
    postgres_user: str = "tender_lens"
    postgres_password: SecretStr = SecretStr("")

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # S3 (Garage)
    s3_endpoint_url: str = "http://garage:3900"
    s3_region: str = "garage"
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket_snapshots: str = "snapshots"
    s3_bucket_reports: str = "reports"

    # Garage admin API (инициализация хранилища)
    garage_admin_url: str = "http://garage:3903"
    garage_admin_token: SecretStr = SecretStr("")
    garage_capacity_gb: int = 10

    # LLM
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:4b"
    ollama_num_ctx: int = 8192
    # Предел длины ответа (токены): защита от зацикливания мелкой модели на длинных списках
    ollama_num_predict: int = 3072
    llm_max_concurrency: int = 1

    # Поиск и сбор данных
    searxng_url: str = "http://searxng:8080"
    http_user_agent: str = "Mozilla/5.0 (compatible; TenderLens/0.1)"
    http_timeout_seconds: float = 20.0
    # Домены, для которых доверяем Russian Trusted Root CA (через запятую; пусто — не доверять)
    ru_trusted_ca_hosts: str = "gov.ru"

    @property
    def ru_trusted_ca_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.ru_trusted_ca_hosts.split(",") if h.strip()]

    eis_token: SecretStr | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
