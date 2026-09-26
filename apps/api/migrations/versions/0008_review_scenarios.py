"""Append-only review events and versioned provisional scenario snapshots.

Revision ID: 0008_review_scenarios
Revises: 0007_hardening
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_review_scenarios"
down_revision = "0007_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_runs",
        sa.Column(
            "retry_of_id", sa.String(length=36), sa.ForeignKey("extraction_runs.id"), nullable=True
        ),
    )
    op.create_index("ix_extraction_runs_retry_of_id", "extraction_runs", ["retry_of_id"])
    op.create_table(
        "extraction_review_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id", sa.String(length=36), sa.ForeignKey("extraction_runs.id"), nullable=False
        ),
        sa.Column(
            "issue_id", sa.String(length=36), sa.ForeignKey("extraction_issues.id"), nullable=True
        ),
        sa.Column(
            "requirement_id",
            sa.String(length=36),
            sa.ForeignKey("extracted_requirements.id"),
            nullable=True,
        ),
        sa.Column(
            "source_span_id", sa.String(length=36), sa.ForeignKey("source_spans.id"), nullable=True
        ),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("reviewer", sa.String(length=80), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("before_value", sa.JSON(), nullable=True),
        sa.Column("after_value", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name, columns in (
        ("ix_extraction_review_events_run_id", ["run_id"]),
        ("ix_extraction_review_events_issue_id", ["issue_id"]),
        ("ix_extraction_review_events_requirement_id", ["requirement_id"]),
        ("ix_extraction_review_events_source_span_id", ["source_span_id"]),
    ):
        op.create_index(name, "extraction_review_events", columns)
    op.create_table(
        "scenario_workspaces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "source_extraction_run_id",
            sa.String(length=36),
            sa.ForeignKey("extraction_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "catalog_version_id",
            sa.String(length=36),
            sa.ForeignKey("catalog_versions.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_scenario_workspaces_source_extraction_run_id",
        "scenario_workspaces",
        ["source_extraction_run_id"],
    )
    op.create_index(
        "ix_scenario_workspaces_catalog_version_id", "scenario_workspaces", ["catalog_version_id"]
    )
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "scenario_id",
            sa.String(length=36),
            sa.ForeignKey("scenario_workspaces.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("identity", sa.String(length=64), nullable=False, unique=True),
        sa.Column("intent", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("reviewer", sa.String(length=80), nullable=False),
        sa.Column(
            "source_extraction_run_id",
            sa.String(length=36),
            sa.ForeignKey("extraction_runs.id"),
            nullable=False,
        ),
        sa.Column(
            "scenario_extraction_run_id",
            sa.String(length=36),
            sa.ForeignKey("extraction_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "analysis_id",
            sa.String(length=36),
            sa.ForeignKey("analysis_runs.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name, columns in (
        ("ix_scenario_versions_scenario_id", ["scenario_id"]),
        ("ix_scenario_versions_source_extraction_run_id", ["source_extraction_run_id"]),
    ):
        op.create_index(name, "scenario_versions", columns)
    op.create_table(
        "scenario_assumptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "scenario_version_id",
            sa.String(length=36),
            sa.ForeignKey("scenario_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            sa.String(length=36),
            sa.ForeignKey("extracted_requirements.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("attribute", sa.String(length=80), nullable=False),
        sa.Column("operator", sa.String(length=24), nullable=False),
        sa.Column("original_value", sa.JSON(), nullable=False),
        sa.Column("original_unit", sa.String(length=32), nullable=False),
        sa.Column("normalized_value", sa.JSON(), nullable=False),
        sa.Column("normalized_unit", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("provenance", sa.String(length=80), nullable=False),
    )
    op.create_index(
        "ix_scenario_assumptions_scenario_version_id",
        "scenario_assumptions",
        ["scenario_version_id"],
    )


def downgrade() -> None:
    op.drop_table("scenario_assumptions")
    op.drop_table("scenario_versions")
    op.drop_table("scenario_workspaces")
    op.drop_table("extraction_review_events")
    op.drop_index("ix_extraction_runs_retry_of_id", table_name="extraction_runs")
    op.drop_column("extraction_runs", "retry_of_id")
