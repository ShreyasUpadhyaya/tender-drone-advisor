"""Safe stale-job marking; recovery requires replaying the original idempotent request."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisRun
from app.extraction.models import ExtractionRun
from app.models import IngestionJob, ProcessingState
from app.rag.models import RagJob


def mark_stale_jobs(db: Session, stale_seconds: int) -> dict[str, int]:
    """Terminally mark abandoned jobs without re-running providers automatically.

    Queue delivery remains at-least-once. Idempotency identities make an explicit
    replay safe; this process intentionally never claims exactly-once delivery.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    updated = {"ingestion": 0, "extraction": 0, "analysis": 0, "rag": 0}
    for row in db.scalars(
        select(IngestionJob).where(
            IngestionJob.state.in_([ProcessingState.UPLOADED, ProcessingState.PROCESSING]),
            IngestionJob.updated_at < cutoff,
        )
    ):
        row.state, row.error_code = ProcessingState.FAILED, "job_stale"
        row.error_message = "Processing timed out. Repeat the idempotent request to recover safely."
        updated["ingestion"] += 1
    for row in db.scalars(
        select(ExtractionRun).where(
            ExtractionRun.state.in_(["queued", "running"]), ExtractionRun.created_at < cutoff
        )
    ):
        row.state, row.error_code, row.finished_at = "failed", "job_stale", datetime.now(UTC)
        updated["extraction"] += 1
    for row in db.scalars(
        select(AnalysisRun).where(
            AnalysisRun.state.in_(["queued", "running"]), AnalysisRun.created_at < cutoff
        )
    ):
        row.state, row.error_code, row.finished_at = "failed", "job_stale", datetime.now(UTC)
        updated["analysis"] += 1
    for row in db.scalars(
        select(RagJob).where(RagJob.state.in_(["queued", "running"]), RagJob.created_at < cutoff)
    ):
        row.state, row.error_code = "failed", "job_stale"
        updated["rag"] += 1
    db.commit()
    return updated
