"""Add admin-owned inventory overlays and review revisions.

Revision ID: 0010_manual_inventory
Revises: 0009_scenario_prefs
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_manual_inventory"
down_revision = "0009_scenario_prefs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_review_events",
        sa.Column("supersedes_event_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_event_supersedes",
        "extraction_review_events",
        "extraction_review_events",
        ["supersedes_event_id"],
        ["id"],
    )
    op.create_index(
        "ix_extraction_review_events_supersedes_event_id",
        "extraction_review_events",
        ["supersedes_event_id"],
        unique=True,
    )
    op.add_column(
        "scenario_versions",
        sa.Column(
            "inventory_version_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )
    op.create_table(
        "inventory_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("catalog_item_id", sa.String(length=36), nullable=True),
        sa.Column("sku", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("manufacturer", sa.String(length=160), nullable=False),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.Column("owner_subject", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["catalog_item_id"], ["catalog_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "sku", name="uq_inventory_workspace_sku"),
    )
    op.create_index("ix_inventory_records_workspace_id", "inventory_records", ["workspace_id"])
    op.create_index(
        "ix_inventory_records_catalog_item_id", "inventory_records", ["catalog_item_id"]
    )
    op.create_index("ix_inventory_records_category", "inventory_records", ["category"])
    op.create_index("ix_inventory_records_owner_subject", "inventory_records", ["owner_subject"])
    op.create_table(
        "inventory_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("identity", sa.String(length=64), nullable=False),
        sa.Column("on_hand_quantity", sa.Integer(), nullable=False),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column("expected_on", sa.Date(), nullable=True),
        sa.Column("location", sa.String(length=160), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("extraction_run_id", sa.String(length=36), nullable=True),
        sa.Column("requirement_ids", sa.JSON(), nullable=False),
        sa.Column("requirement_categories", sa.JSON(), nullable=False),
        sa.Column("recorded_by_subject", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["record_id"], ["inventory_records.id"]),
        sa.ForeignKeyConstraint(["extraction_run_id"], ["extraction_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "version_number", name="uq_inventory_record_version"),
        sa.UniqueConstraint("identity", name="uq_inventory_version_identity"),
    )
    op.create_index("ix_inventory_versions_record_id", "inventory_versions", ["record_id"])
    op.create_index(
        "ix_inventory_versions_extraction_run_id", "inventory_versions", ["extraction_run_id"]
    )
    op.create_index(
        "ix_inventory_versions_recorded_by_subject", "inventory_versions", ["recorded_by_subject"]
    )


def downgrade() -> None:
    op.drop_table("inventory_versions")
    op.drop_table("inventory_records")
    op.drop_column("scenario_versions", "inventory_version_ids")
    op.drop_index(
        "ix_extraction_review_events_supersedes_event_id", table_name="extraction_review_events"
    )
    op.drop_constraint("fk_review_event_supersedes", "extraction_review_events", type_="foreignkey")
    op.drop_column("extraction_review_events", "supersedes_event_id")
