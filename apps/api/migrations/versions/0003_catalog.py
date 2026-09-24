"""Versioned synthetic drone capability catalog."""

import sqlalchemy as sa
from alembic import op

revision = "0003_catalog"
down_revision = "0002_extraction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.String(40), unique=True, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
    )
    op.create_table(
        "catalog_suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("supplier_code", sa.String(80), unique=True, nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "catalog_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "catalog_version_id",
            sa.String(36),
            sa.ForeignKey("catalog_versions.id"),
            nullable=False,
        ),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("catalog_suppliers.id")),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("item_version", sa.Integer(), nullable=False),
        sa.Column("manufacturer", sa.String(160), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("lifecycle_status", sa.String(20), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("cost_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("inventory_qty", sa.Integer(), nullable=False),
        sa.Column("lead_time_days", sa.Integer(), nullable=False),
        sa.Column("availability", sa.String(20), nullable=False),
        sa.Column("specs", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("sku", "item_version", name="uq_catalog_sku_version"),
    )
    op.create_table(
        "catalog_prices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("catalog_items.id"), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date()),
    )
    op.create_table(
        "catalog_compatibility_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("from_item_id", sa.String(36), sa.ForeignKey("catalog_items.id"), nullable=False),
        sa.Column("to_item_id", sa.String(36), sa.ForeignKey("catalog_items.id"), nullable=False),
        sa.Column("rule_type", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "from_item_id", "to_item_id", "rule_type", name="uq_compatibility_pair"
        ),
    )
    for table, column in (
        ("catalog_items", "catalog_version_id"),
        ("catalog_items", "sku"),
        ("catalog_items", "category"),
        ("catalog_items", "availability"),
        ("catalog_compatibility_rules", "from_item_id"),
        ("catalog_compatibility_rules", "to_item_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, column in (
        ("catalog_items", "catalog_version_id"),
        ("catalog_items", "sku"),
        ("catalog_items", "category"),
        ("catalog_items", "availability"),
        ("catalog_compatibility_rules", "from_item_id"),
        ("catalog_compatibility_rules", "to_item_id"),
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
    for table in (
        "catalog_compatibility_rules",
        "catalog_prices",
        "catalog_items",
        "catalog_suppliers",
        "catalog_versions",
    ):
        op.drop_table(table)
