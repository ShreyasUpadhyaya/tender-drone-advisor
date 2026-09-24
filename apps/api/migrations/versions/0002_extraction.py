"""Versioned extraction runs, validated requirements, evidence, reviews and node audit."""

import sqlalchemy as sa
from alembic import op

revision = "0002_extraction"
down_revision = "0001_document_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.String(36),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), unique=True, nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("model_config", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("queue_job_id", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "extracted_requirements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("extraction_runs.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_requirement_ordinal"),
    )
    op.create_table(
        "requirement_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "requirement_id",
            sa.String(36),
            sa.ForeignKey("extracted_requirements.id"),
            nullable=False,
        ),
        sa.Column("span_id", sa.String(36), sa.ForeignKey("source_spans.id"), nullable=False),
        sa.Column("anchor", sa.JSON(), nullable=False),
    )
    op.create_table(
        "extraction_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("extraction_runs.id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("decision", sa.JSON()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "extraction_node_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("extraction_runs.id"), nullable=False),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("elapsed_ms", sa.Integer()),
        sa.Column("branch", sa.String(40)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("summary", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    for name in (
        "extraction_node_runs",
        "extraction_issues",
        "requirement_evidence",
        "extracted_requirements",
        "extraction_runs",
    ):
        op.drop_table(name)
