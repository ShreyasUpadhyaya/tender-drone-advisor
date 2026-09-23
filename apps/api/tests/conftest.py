import os
from collections.abc import Iterator
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///./tender_advisor_test.db"

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
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


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
