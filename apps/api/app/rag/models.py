from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.db import Base


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector"


class RagJob(Base):
    __tablename__ = "rag_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    job_type: Mapped[str] = mapped_column(String(16))
    namespace: Mapped[str] = mapped_column(String(80), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="queued")
    request: Mapped[dict] = mapped_column(JSON)
    config: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    queue_job_id: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Chunk(Base):
    __tablename__ = "rag_chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    index_id: Mapped[str] = mapped_column(ForeignKey("rag_jobs.id"), index=True)
    namespace: Mapped[str] = mapped_column(String(80), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    document_version_id: Mapped[str] = mapped_column(String(36), index=True)
    catalog_version_id: Mapped[str] = mapped_column(String(36), index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(36), index=True)
    item_id: Mapped[str | None] = mapped_column(String(36), index=True)
    text: Mapped[str] = mapped_column(Text)
    citation: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list] = mapped_column(JSON)
    vector: Mapped[str | None] = mapped_column(Vector().with_variant(Text(), "sqlite"))
    dimensions: Mapped[int] = mapped_column(Integer)


class RetrievalRun(Base):
    __tablename__ = "rag_retrieval_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    index_id: Mapped[str] = mapped_column(ForeignKey("rag_jobs.id"), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), default="completed")
    result: Mapped[dict] = mapped_column(JSON)


class NodeAudit(Base):
    __tablename__ = "rag_node_audits"
    __table_args__ = (UniqueConstraint("run_id", "node", "attempt"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("rag_jobs.id"), index=True)
    node: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    branch: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    summary: Mapped[dict] = mapped_column(JSON)


class CheckpointRecord(Base):
    __tablename__ = "rag_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("rag_jobs.id"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(80))
    encoding: Mapped[str] = mapped_column(String(32))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    meta: Mapped[dict] = mapped_column(JSON)


class CheckpointWrite(Base):
    __tablename__ = "rag_checkpoint_writes"
    run_id: Mapped[str] = mapped_column(ForeignKey("rag_jobs.id"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    write_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(200))
    encoding: Mapped[str] = mapped_column(String(32))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
