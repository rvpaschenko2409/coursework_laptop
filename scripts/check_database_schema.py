"""Generate migration SQL without connecting or changing a database."""
from io import StringIO
from pathlib import Path
import argparse
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]

def check():
    stream = StringIO()
    cfg = Config(str(ROOT / "alembic.ini"), output_buffer=stream)
    command.upgrade(cfg, "head", sql=True)
    generated = stream.getvalue()
    source = (ROOT / "db_migrations/versions/0001_initial.sql").read_text(encoding="utf-8")
    exported = (ROOT / "sql/001_initial_schema.sql").read_text(encoding="utf-8")
    if source.strip() != exported.strip():
        raise RuntimeError("DDL не совпадает с зафиксированной миграцией")
    tables = [line.split()[2] for line in source.splitlines() if line.startswith("CREATE TABLE ")]
    if len(tables) != 21 or "INSERT INTO alembic_version" not in generated:
        raise RuntimeError("Неполная миграция")
    print(f"DDL и миграция согласованы. Прикладных таблиц: {len(tables)}.")
    print("Автономная генерация SQL Alembic: успешно.")
    print("Подключение к вашей PostgreSQL и создание таблиц ещё не выполнялись.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.parse_args()
    check()
