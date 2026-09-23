import hashlib
import logging
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, IngestionJob, ProcessingState, SourceSpan
from app.parsers import ParsedChunk, ParseError, parse_document
from app.storage import ObjectStorage

LOGGER = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


class IngestionError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


def validate_upload(
    filename: str | None, content_type: str | None, byte_size: int, max_bytes: int
) -> str:
    suffix = f".{filename.rsplit('.', 1)[-1].lower()}" if filename and "." in filename else ""
    expected_type = ALLOWED_CONTENT_TYPES.get(suffix)
    if expected_type is None:
        raise IngestionError(
            "unsupported_file_type", "Only PDF, DOCX, and TXT tender files are accepted."
        )
    if content_type not in {expected_type, "application/octet-stream"}:
        raise IngestionError(
            "content_type_mismatch", f"Expected {expected_type} for {suffix} files."
        )
    if byte_size == 0:
        raise IngestionError("empty_file", "The uploaded file is empty.")
    if byte_size > max_bytes:
        raise IngestionError("file_too_large", f"The upload exceeds the {max_bytes}-byte limit.")
    return expected_type


def create_or_get_document(
    db: Session,
    storage: ObjectStorage,
    *,
    filename: str,
    content_type: str,
    body: bytes,
) -> tuple[Document, DocumentVersion, IngestionJob, bool]:
    checksum = hashlib.sha256(body).hexdigest()
    existing = db.scalar(select(Document).where(Document.content_sha256 == checksum))
    if existing is not None:
        version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == existing.id)
            .order_by(DocumentVersion.version_number.desc())
        )
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_version_id == version.id)
            .order_by(IngestionJob.created_at.desc())
        )
        assert version is not None and job is not None
        return existing, version, job, True

    document = Document(content_sha256=checksum, state=ProcessingState.UPLOADED)
    db.add(document)
    db.flush()
    storage_key = f"documents/{document.id}/versions/1/original"
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        original_filename=filename,
        content_type=content_type,
        byte_size=len(body),
        storage_key=storage_key,
    )
    job = IngestionJob(document_version=version, state=ProcessingState.UPLOADED)
    db.add_all([version, job])
    storage.put_bytes(storage_key, body, content_type)
    db.commit()
    db.refresh(document)
    db.refresh(version)
    db.refresh(job)
    LOGGER.info(
        "Stored tender document id=%s version=%s bytes=%s", document.id, version.id, len(body)
    )
    return document, version, job, False


def persist_chunks(db: Session, version: DocumentVersion, chunks: list[ParsedChunk]) -> int:
    db.query(SourceSpan).filter(SourceSpan.document_version_id == version.id).delete()
    offset = 0
    for index, chunk in enumerate(chunks):
        end_offset = offset + len(chunk.text)
        db.add(
            SourceSpan(
                document_version_id=version.id,
                page_number=chunk.page_number,
                section_name=chunk.section_name,
                chunk_index=index,
                start_offset=offset,
                end_offset=end_offset,
                extraction_method=chunk.extraction_method,
                text=chunk.text,
            )
        )
        offset = end_offset + 1
    return len(chunks)


def ingest_document(
    document_version_id: str,
    *,
    session_factory: Callable[[], Session],
    storage: ObjectStorage,
) -> None:
    db = session_factory()
    try:
        version = db.get(DocumentVersion, document_version_id)
        if version is None:
            raise IngestionError("document_not_found", "Document version was not found.")
        job = db.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_version_id == version.id)
            .order_by(IngestionJob.created_at.desc())
        )
        if job is None:
            raise IngestionError("job_not_found", "Ingestion job was not found.")
        if job.state == ProcessingState.COMPLETED:
            return
        job.state = ProcessingState.PROCESSING
        version.document.state = ProcessingState.PROCESSING
        db.commit()
        body = storage.get_bytes(version.storage_key)
        chunks = parse_document(body, version.content_type)
        chunk_count = persist_chunks(db, version, chunks)
        job.state = ProcessingState.COMPLETED
        version.document.state = ProcessingState.COMPLETED
        job.error_code = None
        job.error_message = None
        db.commit()
        LOGGER.info("Completed tender ingestion version=%s chunks=%s", version.id, chunk_count)
    except (IngestionError, ParseError) as exc:
        db.rollback()
        _mark_failed(db, document_version_id, "parse_failed", str(exc))
        LOGGER.warning("Tender ingestion failed version=%s code=parse_failed", document_version_id)
    except Exception:
        db.rollback()
        _mark_failed(
            db, document_version_id, "processing_failed", "Document processing failed safely."
        )
        LOGGER.exception("Tender ingestion failed version=%s", document_version_id)
        raise
    finally:
        db.close()


def _mark_failed(db: Session, document_version_id: str, code: str, message: str) -> None:
    version = db.get(DocumentVersion, document_version_id)
    if version is None:
        return
    job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.document_version_id == version.id)
        .order_by(IngestionJob.created_at.desc())
    )
    if job is not None:
        job.state = ProcessingState.FAILED
        job.error_code = code
        job.error_message = message[:500]
    version.document.state = ProcessingState.FAILED
    db.commit()
