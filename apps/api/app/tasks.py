from app.db import SessionLocal
from app.ingestion import ingest_document
from app.observability import stage_timer
from app.storage import get_storage


def extract_requirements_task(run_id: str) -> None:
    from app.extraction.workflow import execute_run

    with stage_timer("extraction", trace_id=run_id, job_id=run_id):
        execute_run(run_id, SessionLocal)


def ingest_document_task(document_version_id: str) -> None:
    with stage_timer("ingestion", job_id=document_version_id):
        ingest_document(document_version_id, session_factory=SessionLocal, storage=get_storage())
