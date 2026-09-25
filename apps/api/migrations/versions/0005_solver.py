"""Immutable deterministic analyses, snapshots, configurations, BOM, decisions and costs."""

import sqlalchemy as sa
from alembic import op

revision = "0005_solver"
down_revision = "0004_catalog_weight_si"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "extraction_run_id", sa.String(36), sa.ForeignKey("extraction_runs.id"), nullable=False
        ),
        sa.Column(
            "catalog_version_id",
            sa.String(36),
            sa.ForeignKey("catalog_versions.id"),
            nullable=False,
        ),
        sa.Column("solver_version", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32)),
        sa.Column("outcome", sa.String(32)),
        sa.Column("queue_job_id", sa.String(80)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "analysis_snapshots",
        sa.Column(
            "analysis_id", sa.String(36), sa.ForeignKey("analysis_runs.id"), primary_key=True
        ),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "analysis_configurations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("platform_id", sa.String(36), sa.ForeignKey("catalog_items.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("analysis_id", "rank", name="uq_analysis_rank"),
    )
    op.create_table(
        "analysis_bom",
        sa.Column(
            "configuration_id",
            sa.String(32),
            sa.ForeignKey("analysis_configurations.id"),
            primary_key=True,
        ),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("catalog_items.id"), primary_key=True),
        sa.Column("price_id", sa.String(36), sa.ForeignKey("catalog_prices.id")),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "analysis_evaluations",
        sa.Column(
            "configuration_id",
            sa.String(32),
            sa.ForeignKey("analysis_configurations.id"),
            primary_key=True,
        ),
        sa.Column(
            "requirement_id",
            sa.String(36),
            sa.ForeignKey("extracted_requirements.id"),
            primary_key=True,
        ),
        sa.Column("result", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "analysis_costs",
        sa.Column(
            "configuration_id",
            sa.String(32),
            sa.ForeignKey("analysis_configurations.id"),
            primary_key=True,
        ),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "analysis_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analysis_runs.id"), nullable=False),
        sa.Column("configuration_id", sa.String(32), sa.ForeignKey("analysis_configurations.id")),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    for table, columns in {
        "analysis_runs": ["extraction_run_id", "catalog_version_id", "state"],
        "analysis_configurations": ["analysis_id"],
        "analysis_issues": ["analysis_id", "configuration_id"],
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION guard_analysis_history() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE aid text; terminal boolean; obj jsonb;
        BEGIN
            obj := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
            IF TG_TABLE_NAME = 'analysis_runs' THEN
                IF TG_OP <> 'INSERT' AND OLD.state IN ('completed', 'failed') THEN
                    RAISE EXCEPTION 'terminal analysis is immutable';
                END IF;
            ELSE
                IF TG_TABLE_NAME = 'analysis_snapshots' AND TG_OP <> 'INSERT' THEN
                    RAISE EXCEPTION 'analysis snapshot is immutable';
                END IF;
                IF TG_OP = 'UPDATE' THEN
                    RAISE EXCEPTION 'analysis result rows are immutable';
                END IF;
                aid := obj->>'analysis_id';
                IF aid IS NULL THEN
                    SELECT analysis_id INTO aid FROM analysis_configurations
                    WHERE id = obj->>'configuration_id';
                END IF;
                SELECT state IN ('completed','failed') INTO terminal FROM analysis_runs WHERE id=aid;
                IF terminal THEN RAISE EXCEPTION 'terminal analysis children are immutable'; END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
        """)
        for table in TABLES:
            op.execute(
                f"CREATE TRIGGER protect_{table} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION guard_analysis_history()"
            )


TABLES = [
    "analysis_runs",
    "analysis_snapshots",
    "analysis_configurations",
    "analysis_bom",
    "analysis_evaluations",
    "analysis_costs",
    "analysis_issues",
]


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION guard_analysis_history()")
