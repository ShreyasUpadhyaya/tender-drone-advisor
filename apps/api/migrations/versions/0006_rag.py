"""Snapshot-isolated pgvector indexes, retrievals, graph reports and durable checkpoints."""

from alembic import op

revision = "0006_rag"
down_revision = "0005_solver"
branch_labels = None
depends_on = None

DDL = [
    """CREATE TABLE rag_jobs (
    id VARCHAR(36) NOT NULL,
    identity VARCHAR(64) NOT NULL,
    job_type VARCHAR(16) NOT NULL,
    namespace VARCHAR(80) NOT NULL,
    analysis_id VARCHAR(36) NOT NULL,
    state VARCHAR(24) NOT NULL,
    request JSON NOT NULL,
    config JSON NOT NULL,
    result JSON NOT NULL,
    error_code VARCHAR(80),
    queue_job_id VARCHAR(80),
    attempts INTEGER NOT NULL,
    decision JSON,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (identity),
    FOREIGN KEY(analysis_id) REFERENCES analysis_runs (id)
)""",
    """CREATE INDEX ix_rag_jobs_analysis_id ON rag_jobs (analysis_id)""",
    """CREATE INDEX ix_rag_jobs_namespace ON rag_jobs (namespace)""",
    """CREATE TABLE rag_chunks (
    id VARCHAR(64) NOT NULL,
    index_id VARCHAR(36) NOT NULL,
    namespace VARCHAR(80) NOT NULL,
    kind VARCHAR(24) NOT NULL,
    document_version_id VARCHAR(36) NOT NULL,
    catalog_version_id VARCHAR(36) NOT NULL,
    requirement_id VARCHAR(36),
    item_id VARCHAR(36),
    text TEXT NOT NULL,
    citation JSON NOT NULL,
    embedding JSON NOT NULL,
    vector vector,
    dimensions INTEGER NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(index_id) REFERENCES rag_jobs (id)
)""",
    """CREATE INDEX ix_rag_chunks_catalog_version_id ON rag_chunks (catalog_version_id)""",
    """CREATE INDEX ix_rag_chunks_document_version_id ON rag_chunks (document_version_id)""",
    """CREATE INDEX ix_rag_chunks_index_id ON rag_chunks (index_id)""",
    """CREATE INDEX ix_rag_chunks_item_id ON rag_chunks (item_id)""",
    """CREATE INDEX ix_rag_chunks_kind ON rag_chunks (kind)""",
    """CREATE INDEX ix_rag_chunks_namespace ON rag_chunks (namespace)""",
    """CREATE INDEX ix_rag_chunks_requirement_id ON rag_chunks (requirement_id)""",
    """CREATE TABLE rag_retrieval_runs (
    id VARCHAR(36) NOT NULL,
    identity VARCHAR(64) NOT NULL,
    index_id VARCHAR(36) NOT NULL,
    request JSON NOT NULL,
    state VARCHAR(24) NOT NULL,
    result JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (identity),
    FOREIGN KEY(index_id) REFERENCES rag_jobs (id)
)""",
    """CREATE INDEX ix_rag_retrieval_runs_index_id ON rag_retrieval_runs (index_id)""",
    """CREATE TABLE rag_node_audits (
    id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    node VARCHAR(80) NOT NULL,
    attempt INTEGER NOT NULL,
    status VARCHAR(24) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    elapsed_ms INTEGER,
    branch VARCHAR(80),
    error_code VARCHAR(80),
    summary JSON NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (run_id, node, attempt),
    FOREIGN KEY(run_id) REFERENCES rag_jobs (id)
)""",
    """CREATE INDEX ix_rag_node_audits_run_id ON rag_node_audits (run_id)""",
    """CREATE TABLE rag_checkpoints (
    run_id VARCHAR(36) NOT NULL,
    namespace VARCHAR(200) NOT NULL,
    checkpoint_id VARCHAR(80) NOT NULL,
    parent_id VARCHAR(80),
    encoding VARCHAR(32) NOT NULL,
    payload BYTEA NOT NULL,
    meta JSON NOT NULL,
    PRIMARY KEY (run_id, namespace, checkpoint_id),
    FOREIGN KEY(run_id) REFERENCES rag_jobs (id)
)""",
    """CREATE TABLE rag_checkpoint_writes (
    run_id VARCHAR(36) NOT NULL,
    namespace VARCHAR(200) NOT NULL,
    checkpoint_id VARCHAR(80) NOT NULL,
    task_id VARCHAR(80) NOT NULL,
    write_index INTEGER NOT NULL,
    channel VARCHAR(200) NOT NULL,
    encoding VARCHAR(32) NOT NULL,
    payload BYTEA NOT NULL,
    PRIMARY KEY (run_id, namespace, checkpoint_id, task_id, write_index),
    FOREIGN KEY(run_id) REFERENCES rag_jobs (id)
)""",
]


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    for statement in DDL:
        op.execute(statement)
    op.execute(
        "ALTER TABLE rag_chunks ADD CONSTRAINT ck_rag_vector_dimensions CHECK (vector_dims(vector) = dimensions)"
    )
    op.execute("""
        CREATE FUNCTION guard_rag_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'rag_jobs' THEN
                IF OLD.state IN ('completed','failed') THEN
                    RAISE EXCEPTION 'terminal RAG job is immutable';
                END IF;
            ELSIF TG_TABLE_NAME = 'rag_node_audits' THEN
                IF OLD.status <> 'running' THEN RAISE EXCEPTION 'terminal node audit is immutable'; END IF;
            ELSE
                RAISE EXCEPTION 'RAG evidence and retrieval history is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$;
    """)
    for table in ("rag_jobs", "rag_chunks", "rag_retrieval_runs", "rag_node_audits"):
        op.execute(
            f"CREATE TRIGGER protect_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_rag_history()"
        )


def downgrade():
    for table in (
        "rag_checkpoint_writes",
        "rag_checkpoints",
        "rag_node_audits",
        "rag_retrieval_runs",
        "rag_chunks",
        "rag_jobs",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION guard_rag_history()")
