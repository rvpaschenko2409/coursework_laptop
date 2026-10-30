"""Opt-in PostgreSQL constraint tests in a fresh temporary schema."""
import json
import os
from pathlib import Path
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_database_ddl_matches_revision():
    assert (ROOT / "sql/001_initial_schema.sql").read_bytes() == (
        ROOT / "db_migrations/versions/0001_initial.sql").read_bytes()


def test_database_offline_migration():
    pytest.importorskip("alembic")
    from scripts.check_database_schema import check
    check()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                    reason="PostgreSQL integration requires TEST_DATABASE_URL")
def test_database_postgresql_constraints():
    psycopg = pytest.importorskip("psycopg")
    from psycopg import sql
    url = os.environ["TEST_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    schema = "coursework_test_" + uuid.uuid4().hex
    # Only the randomly generated schema is created/dropped. No existing tables are modified.
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            ddl = (ROOT / "db_migrations/versions/0001_initial.sql").read_text(encoding="utf-8")
            connection.execute(ddl, prepare=False)
            cases = json.loads((ROOT / "tests/database_schema_cases.json").read_text(encoding="utf-8"))
            for case in cases:
                expected = case.get("sqlstate")
                if expected:
                    with pytest.raises(psycopg.Error) as error:
                        with connection.transaction():
                            connection.execute(case["sql"], prepare=False)
                    assert error.value.sqlstate == expected, case["name"]
                else:
                    connection.execute(case["sql"], prepare=False)
                if case["name"] == "manual_override_history":
                    assert connection.execute(
                        "SELECT old_value,new_value,prediction_id FROM score_history"
                    ).fetchall() == [(1000.0,1100.0,1)]
                if case["name"] == "project_cascade":
                    assert connection.execute(
                        "SELECT count(*) FROM alternatives WHERE project_id=2"
                    ).fetchone()[0] == 1
            connection.execute((ROOT / "db_migrations/versions/0001_down.sql").read_text(encoding="utf-8"), prepare=False)
            connection.execute(ddl, prepare=False)
        finally:
            connection.execute("SET search_path TO public")
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
