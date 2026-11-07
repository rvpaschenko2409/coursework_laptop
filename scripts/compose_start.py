"""Initialize the isolated Compose database or serve the web application."""
import argparse
import os

import psycopg
import uvicorn
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy import URL

from scripts.grant_demo_access import grant_access
from scripts.register_ml import load_bundle, register
from scripts.seed_demo import load_payload, seed
from web_app.database import Database, grant_model_explanation_permissions
from web_app.main import create_app
from web_app.security import PH, validate_password

DB_NAME = "coursework_laptop"
APP_ROLE = "coursework_app"
DEMO_EMAIL = "docker-demo@example.com"


def connect_info(user: str, password: str) -> str:
    return make_conninfo(
        host="db", dbname=DB_NAME, user=user, password=password, connect_timeout=10
    )


def setup() -> None:
    admin_password = os.environ["COURSEWORK_DB_PASSWORD"]
    app_password = os.environ["COURSEWORK_APP_PASSWORD"]
    demo_password = os.environ["COURSEWORK_DEMO_PASSWORD"]
    validate_password(demo_password)
    os.environ["DATABASE_URL"] = URL.create(
        "postgresql+psycopg", username="postgres", password=admin_password,
        host="db", database=DB_NAME,
    ).render_as_string(hide_password=False)
    core = Config("alembic.ini")

    with psycopg.connect(
        connect_info("postgres", admin_password), row_factory=dict_row
    ) as connection:
        connection.execute("SELECT pg_advisory_lock(71840605)")
        revision = connection.execute(
            "SELECT to_regclass('public.alembic_version') AS name"
        ).fetchone()["name"]
        connection.commit()
        if revision is None:
            command.upgrade(core, "0001")

        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if version["version_num"] == "0001":
            seed(connection, load_payload())
        connection.commit()
        command.upgrade(core, "0002")
        command.upgrade(Config("web_alembic.ini"), "web0004")

        with connection.transaction():
            role = connection.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
            ).fetchone()
            if role is None:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(APP_ROLE), sql.Literal(app_password)
                ))
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute("GRANT USAGE ON SCHEMA public TO coursework_app")
            grant_model_explanation_permissions(connection)

            demo = connection.execute(
                "SELECT id FROM users WHERE email = %s", (DEMO_EMAIL,)
            ).fetchone()
            if demo is None:
                register(connection, load_bundle())
                connection.execute(
                    "INSERT INTO users(email, name, password_hash) VALUES (%s, %s, %s)",
                    (DEMO_EMAIL, "Учебный аналитик", PH.hash(demo_password)),
                )
                grant_access(connection, DEMO_EMAIL)

    print("PostgreSQL, миграции и демонстрационный проект готовы.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "web"))
    if parser.parse_args().mode == "setup":
        setup()
        return
    database = Database(connect_info(APP_ROLE, os.environ["COURSEWORK_APP_PASSWORD"]))
    uvicorn.run(create_app(database), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
