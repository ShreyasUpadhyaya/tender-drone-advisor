from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    demo_mode: bool = True
    auth_mode: str = "demo"
    oidc_issuer_url: str = ""
    oidc_audience: str = ""
    default_workspace_id: str = "local-demo"
    cors_origins: str = "http://localhost:3000"
    database_url: str = "postgresql+psycopg://tender_advisor:replace-with-local-password@localhost:5432/tender_advisor"
    # psycopg pipeline mode and server-side prepared statements can conflict
    # across pooled PostgreSQL connections. None disables server preparation.
    psycopg_prepare_threshold: int | None = None
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "tender-documents"
    s3_access_key: str = "replace-with-local-access-key"
    s3_secret_key: str = "replace-with-local-secret-key"
    max_upload_bytes: int = 20 * 1024 * 1024
    max_document_pages: int = Field(default=250, ge=1, le=5000)
    upload_rate_limit_per_minute: int = Field(default=12, ge=1, le=1000)
    generation_rate_limit_per_minute: int = Field(default=30, ge=1, le=5000)
    job_stale_seconds: int = Field(default=900, ge=60, le=86400)
    ingestion_queue: str = "tender_ingestion"
    llm_provider: str = "fake"
    llm_model: str = "fixture-v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_max_tokens: int = Field(default=4096, ge=256, le=32768)
    llm_timeout_seconds: int = Field(default=60, ge=1, le=120)
    extraction_max_repairs: int = Field(default=1, ge=0, le=1)
    extraction_transient_retries: int = Field(default=1, ge=0, le=3)
    extraction_confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    extraction_batch_chars: int = Field(default=12000, ge=100, le=50000)
    extraction_max_batches: int = Field(default=32, ge=1, le=100)
    rag_namespace: str = "local-demo"
    rag_embedding_provider: str = "fake"
    rag_embedding_model: str = "hash-v1"
    rag_embedding_dimensions: int = Field(default=256, ge=64, le=1536)
    rag_report_provider: str = "fake"
    rag_report_model: str = "fixture-report-v1"
    rag_external_enabled: bool = False
    log_level: str = "INFO"
    otel_exporter_otlp_endpoint: str = ""

    @field_validator("psycopg_prepare_threshold", mode="before")
    @classmethod
    def empty_prepare_threshold_is_disabled(cls, value: object) -> object:
        return None if value == "" else value

    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def local_demo_enabled(self) -> bool:
        """Demo mode is deliberately unavailable in a production process."""
        return self.demo_mode and self.app_env.lower() not in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
