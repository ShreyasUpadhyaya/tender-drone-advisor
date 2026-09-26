"""C08 security workspace and audit metadata.

Revision ID: 0007_hardening
Revises: 0006_rag
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_hardening"
down_revision = "0006_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("workspace_id", sa.String(length=80), nullable=True))
    op.create_index("ix_documents_workspace_id", "documents", ["workspace_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("workspace_id", sa.String(length=80), nullable=False),
        sa.Column("actor_subject", sa.String(length=160), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=60), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column("trace_id", sa.String(length=80), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False, server_default="success"),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name, columns in (
        ("ix_audit_events_workspace_id", ["workspace_id"]),
        ("ix_audit_events_actor_subject", ["actor_subject"]),
        ("ix_audit_events_action", ["action"]),
        ("ix_audit_events_resource_id", ["resource_id"]),
        ("ix_audit_events_request_id", ["request_id"]),
        ("ix_audit_events_trace_id", ["trace_id"]),
    ):
        op.create_index(name, "audit_events", columns)


def downgrade() -> None:
    for name in (
        "ix_audit_events_trace_id",
        "ix_audit_events_request_id",
        "ix_audit_events_resource_id",
        "ix_audit_events_action",
        "ix_audit_events_actor_subject",
        "ix_audit_events_workspace_id",
    ):
        op.drop_index(name, table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_documents_workspace_id", table_name="documents")
    op.drop_column("documents", "workspace_id")
