import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

TEST_DATABASE = Path(__file__).resolve().parents[1] / f"tender_advisor_test_{os.getpid()}.db"
os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{TEST_DATABASE.as_posix()}"
os.environ["LLM_PROVIDER"] = "fake"
os.environ["LLM_MODEL"] = "fixture-v1"
os.environ["LLM_API_KEY"] = ""
os.environ["RAG_EMBEDDING_PROVIDER"] = "fake"
os.environ["RAG_EMBEDDING_MODEL"] = "hash-v1"
os.environ["RAG_REPORT_PROVIDER"] = "fake"
os.environ["RAG_REPORT_MODEL"] = "fixture-report-v1"
os.environ["RAG_EXTERNAL_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.routes.documents import get_queue
from app.storage import get_storage


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = body

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


class FakeQueue:
    def enqueue(self, *_: object, **__: object) -> SimpleNamespace:
        return SimpleNamespace(id="fake-rq-job")


@pytest.fixture(autouse=True)
def database() -> Iterator[None]:
    # This is the explicitly configured disposable SQLite test artifact. Removing
    # it avoids stale/partial schemas after interrupted test runs without ever
    # touching a configured application database.
    engine.dispose()
    for artifact in (TEST_DATABASE, Path(f"{TEST_DATABASE}-journal"), Path(f"{TEST_DATABASE}-wal")):
        artifact.unlink(missing_ok=True)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()
    for artifact in (TEST_DATABASE, Path(f"{TEST_DATABASE}-journal"), Path(f"{TEST_DATABASE}-wal")):
        artifact.unlink(missing_ok=True)


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def client(storage: InMemoryStorage) -> Iterator[TestClient]:
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = FakeQueue
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
