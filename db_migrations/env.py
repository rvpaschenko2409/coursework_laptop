"""URL from environment only; offline generation does not need credentials."""
import os
from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

def run():
    if context.is_offline_mode():
        context.configure(url="postgresql+psycopg://", literal_binds=True, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Задайте DATABASE_URL для отдельной базы coursework_laptop.")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Нужен URL postgresql+psycopg://; другая СУБД не поддерживается.")
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

run()
