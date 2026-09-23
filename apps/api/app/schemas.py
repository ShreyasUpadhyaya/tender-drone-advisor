from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ProcessingState


class ErrorResponse(BaseModel):
    error: str
    detail: str


class SourceReference(BaseModel):
    document_id: str
    document_version_id: str
    span_id: str
    page_number: int | None
    section_name: str | None
    chunk_index: int
    start_offset: int
    end_offset: int
    extraction_method: str


class SourceSpanResponse(SourceReference):
    text: str


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    document_version_id: str
    job_id: str
    state: ProcessingState
    original_filename: str
    content_type: str
    byte_size: int
    source_span_count: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    idempotent: bool = False


class UploadResponse(DocumentStatusResponse):
    pass


class SourceSpanListResponse(BaseModel):
    document_id: str
    document_version_id: str
    spans: list[SourceReference] = Field(default_factory=list)
