from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
