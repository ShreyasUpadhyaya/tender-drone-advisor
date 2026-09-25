"""Fresh PostgreSQL migration gate; creates and drops ONLY a unique test database."""

import os
import re
import subprocess
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.settings import get_settings


def main():
    source_url = make_url(get_settings().database_url)
    name = "c05_migration_test_" + uuid4().hex
    assert re.fullmatch(r"c05_migration_test_[a-f0-9]{32}", name)
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
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0005_solver"
            assert conn.scalar(text("SELECT count(*) FROM analysis_runs")) == 0
            triggers = conn.scalar(
                text("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'protect_analysis_%'")
            )
            assert triggers == 7
        print("fresh_postgres_migration=passed; head=0005_solver; history_triggers=7")
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
