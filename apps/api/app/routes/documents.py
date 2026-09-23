import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion import IngestionError, create_or_get_document, validate_upload
from app.models import Document, DocumentVersion, IngestionJob, SourceSpan
from app.schemas import (
    DocumentStatusResponse,
    ErrorResponse,
    SourceReference,
    SourceSpanListResponse,
    SourceSpanResponse,
    UploadResponse,
)
from app.settings import get_settings
from app.storage import ObjectStorage, get_storage

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/documents", tags=["documents"])


def get_queue() -> Queue:
    settings = get_settings()
    return Queue(settings.ingestion_queue, connection=Redis.from_url(settings.redis_url))


def _status_response(
    document: Document,
    version: DocumentVersion,
    job: IngestionJob,
    span_count: int,
    *,
    idempotent: bool = False,
) -> DocumentStatusResponse:
    return DocumentStatusResponse(
        document_id=document.id,
        document_version_id=version.id,
        job_id=job.id,
        state=job.state,
        original_filename=version.original_filename,
        content_type=version.content_type,
        byte_size=version.byte_size,
        source_span_count=span_count,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        idempotent=idempotent,
    )


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: ObjectStorage = Depends(get_storage),
    queue: Queue = Depends(get_queue),
) -> UploadResponse:
    settings = get_settings()
    body = await file.read(settings.max_upload_bytes + 1)
    try:
        content_type = validate_upload(
            file.filename, file.content_type, len(body), settings.max_upload_bytes
        )
        document, version, job, idempotent = create_or_get_document(
            db,
            storage,
            filename=file.filename or "unnamed",
            content_type=content_type,
            body=body,
        )
    except IngestionError as exc:
        status_code = 413 if exc.code == "file_too_large" else 415
        raise HTTPException(
            status_code=status_code, detail={"error": exc.code, "detail": exc.detail}
        ) from exc

    span_count = (
        db.scalar(
            select(func.count())
            .select_from(SourceSpan)
            .where(SourceSpan.document_version_id == version.id)
        )
        or 0
    )
    if idempotent:
        response.status_code = status.HTTP_200_OK
        return UploadResponse(
            **_status_response(document, version, job, span_count, idempotent=True).model_dump()
        )

    try:
        queued_job = queue.enqueue("app.tasks.ingest_document_task", version.id, job_timeout=120)
        job.queue_job_id = queued_job.id
        db.commit()
    except Exception as exc:
        job.error_code = "queue_unavailable"
        job.error_message = "The document was stored but could not be queued for processing."
        db.commit()
        LOGGER.exception("Unable to queue tender document version=%s", version.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "queue_unavailable", "detail": job.error_message},
        ) from exc

    response = UploadResponse(**_status_response(document, version, job, span_count).model_dump())
    return response


@router.get(
    "/{document_id}",
    response_model=DocumentStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_document(document_id: str, db: Session = Depends(get_db)) -> DocumentStatusResponse:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "detail": "Document was not found."}
        )
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
    )
    job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_version_id == version.id)
        .order_by(IngestionJob.created_at.desc())
    )
    span_count = (
        db.scalar(
            select(func.count())
            .select_from(SourceSpan)
            .where(SourceSpan.document_version_id == version.id)
        )
        or 0
    )
    assert version is not None and job is not None
    return _status_response(document, version, job, span_count)


@router.get(
    "/{document_id}/spans",
    response_model=SourceSpanListResponse,
    responses={404: {"model": ErrorResponse}},
)
def list_source_spans(document_id: str, db: Session = Depends(get_db)) -> SourceSpanListResponse:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "detail": "Document was not found."}
        )
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_number.desc())
    )
    assert version is not None
    spans = db.scalars(
        select(SourceSpan)
        .where(SourceSpan.document_version_id == version.id)
        .order_by(SourceSpan.chunk_index)
    ).all()
    return SourceSpanListResponse(
        document_id=document.id,
        document_version_id=version.id,
        spans=[
            SourceReference(
                document_id=document.id,
                document_version_id=version.id,
                span_id=span.id,
                page_number=span.page_number,
                section_name=span.section_name,
                chunk_index=span.chunk_index,
                start_offset=span.start_offset,
                end_offset=span.end_offset,
                extraction_method=span.extraction_method,
            )
            for span in spans
        ],
    )


@router.get(
    "/{document_id}/spans/{span_id}",
    response_model=SourceSpanResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_source_span(
    document_id: str, span_id: str, db: Session = Depends(get_db)
) -> SourceSpanResponse:
    document = db.get(Document, document_id)
    span = db.get(SourceSpan, span_id)
    if document is None or span is None:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "detail": "Source span was not found."}
        )
    version = db.get(DocumentVersion, span.document_version_id)
    if version is None or version.document_id != document.id:
        raise HTTPException(
            status_code=404, detail={"error": "not_found", "detail": "Source span was not found."}
        )
    return SourceSpanResponse(
        document_id=document.id,
        document_version_id=version.id,
        span_id=span.id,
        page_number=span.page_number,
        section_name=span.section_name,
        chunk_index=span.chunk_index,
        start_offset=span.start_offset,
        end_offset=span.end_offset,
        extraction_method=span.extraction_method,
        text=span.text,
    )
