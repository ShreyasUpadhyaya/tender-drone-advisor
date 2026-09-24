from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://tender_advisor:replace-with-local-password@localhost:5432/tender_advisor"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket: str = "tender-documents"
    s3_access_key: str = "replace-with-local-access-key"
    s3_secret_key: str = "replace-with-local-secret-key"
    max_upload_bytes: int = 20 * 1024 * 1024
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
