from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    # psycopg's server prepared statements are not required for this small,
    # transactional workload. Disabling them avoids statement-name collisions
    # when pipeline work is interrupted/replayed through pooled connections.
    return {
        "pool_pre_ping": True,
        "connect_args": {"prepare_threshold": get_settings().psycopg_prepare_threshold},
    }


engine = create_engine(get_settings().database_url, **_engine_options(get_settings().database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
