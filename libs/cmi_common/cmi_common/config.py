"""Centralized settings loaded from the environment (12-factor).

Each service instantiates :func:`get_settings` once and injects it. Values come
from environment variables (see ``.env.example``), never hard-coded.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", extra="ignore")

    bootstrap_servers: str = "kafka:9092"
    client_id: str = "cmi"
    # Consumer group is overridden per-service via env.
    group_id: str = "cmi-default"
    auto_offset_reset: str = "latest"
    enable_idempotence: bool = True
    acks: str = "all"
    max_poll_records: int = 500


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    host: str = "postgres"
    port: int = 5432
    user: str = "cmi"
    password: str = "cmi"
    name: str = "cmi"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False

    @property
    def async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        # Used by Alembic migrations.
        return (
            f"postgresql+psycopg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANTHROPIC_", extra="ignore")

    api_key: str = Field(default="", repr=False)
    haiku_model: str = "claude-haiku-4-5-20251001"
    sonnet_model: str = "claude-sonnet-4-6"
    max_tokens: int = 1024
    # Only analyses at/above this score are escalated to Sonnet.
    escalation_threshold: int = 75
    # --- CLI (subscription) transport ---
    # transport selects the Claude backend: "api" (SDK), "cli" (claude -p), "stub".
    transport: str = "api"
    cli_path: str = "claude"
    cli_timeout_ms: int = 120000
    cli_concurrency: int = 4


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    exporter_otlp_endpoint: str = "http://otel-collector:4317"
    sentry_dsn: str = Field(default="", repr=False)
    environment: str = "development"
    metrics_enabled: bool = True
    tracing_enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "cmi-service"
    log_level: str = "INFO"

    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    ai: AISettings = Field(default_factory=AISettings)
    obs: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (one per process)."""
    return Settings()
