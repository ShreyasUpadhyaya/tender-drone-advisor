"""Fresh C08 PostgreSQL migration gate; creates/drops only a unique test DB."""

import os
import re
import subprocess
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.settings import get_settings


def main():
    source_url = make_url(get_settings().database_url)
    name = "c08_migration_test_" + uuid4().hex
    assert re.fullmatch(r"c08_migration_test_[a-f0-9]{32}", name)
    admin = create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM pg_database WHERE datname=:name"), {"name": name}
            )
            == 0
        )
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    fresh_url = source_url.set(database=name)
    fresh = create_engine(fresh_url)
    try:
        env = dict(os.environ)
        env["DATABASE_URL"] = fresh_url.render_as_string(hide_password=False)
        subprocess.run(["alembic", "upgrade", "head"], env=env, check=True)
        with fresh.connect() as conn:
            assert (
                conn.scalar(text("SELECT version_num FROM alembic_version"))
                == "0010_manual_inventory"
            )
            assert conn.scalar(text("SELECT count(*) FROM analysis_runs")) == 0
            triggers = conn.scalar(
                text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'protect_analysis_%'")
            )
            assert triggers == 7
            assert conn.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            assert conn.scalar(text("SELECT count(*) FROM rag_jobs")) == 0
            assert conn.scalar(text("SELECT count(*) FROM audit_events")) == 0
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='documents' AND column_name='workspace_id'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='extraction_runs' AND column_name='retry_of_id'"
                    )
                )
                == 1
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name='scenario_versions' "
                        "AND column_name='component_preferences'"
                    )
                )
                == 1
            )
            assert conn.scalar(text("SELECT count(*) FROM scenario_workspaces")) == 0
            assert conn.scalar(text("SELECT count(*) FROM extraction_review_events")) == 0
            assert conn.scalar(text("SELECT count(*) FROM inventory_records")) == 0
            assert conn.scalar(text("SELECT count(*) FROM inventory_versions")) == 0
        print("fresh_postgres_migration=passed; head=0010_manual_inventory; history_triggers=7")
    finally:
        fresh.dispose()
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}"')
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM pg_database WHERE datname=:name"), {"name": name}
                )
                == 0
            )
        admin.dispose()
        print("isolated_empty_test_database=removed")


if __name__ == "__main__":
    main()
