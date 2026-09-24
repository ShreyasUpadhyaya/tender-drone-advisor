"""Normalize the catalog weight column name for databases created during C04 development."""

import sqlalchemy as sa
from alembic import op

revision = "0004_catalog_weight_si"
down_revision = "0003_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("catalog_items")}
    if "weight_grams" in columns and "weight_kg" not in columns:
        op.alter_column("catalog_items", "weight_grams", new_column_name="weight_kg")
        op.alter_column("catalog_items", "weight_kg", type_=sa.Float())
        op.execute("UPDATE catalog_items SET weight_kg = weight_kg / 1000.0")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("catalog_items")}
    if "weight_kg" in columns and "weight_grams" not in columns:
        op.execute("UPDATE catalog_items SET weight_kg = weight_kg * 1000.0")
        op.alter_column("catalog_items", "weight_kg", type_=sa.Integer())
        op.alter_column("catalog_items", "weight_kg", new_column_name="weight_grams")
