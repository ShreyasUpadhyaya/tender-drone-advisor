"""Persist constrained catalog choices in immutable scenario snapshots.

Revision ID: 0009_scenario_prefs
Revises: 0008_review_scenarios
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# The Alembic version table in existing C01-C07 installations is varchar(32),
# so this identifier intentionally remains below that historical limit.
revision = "0009_scenario_prefs"
down_revision = "0008_review_scenarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add preferences without rewriting already-recorded scenario history."""
    op.add_column(
        "scenario_versions",
        sa.Column(
            "component_preferences",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("scenario_versions", "component_preferences")
