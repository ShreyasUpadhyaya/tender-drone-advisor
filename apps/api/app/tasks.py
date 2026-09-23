from app.db import SessionLocal
from app.ingestion import ingest_document
from app.storage import get_storage


def ingest_document_task(document_version_id: str) -> None:
    ingest_document(document_version_id, session_factory=SessionLocal, storage=get_storage())
