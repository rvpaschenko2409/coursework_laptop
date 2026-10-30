"""Independent auth migrations; preserve the core alembic_version stream."""
import os
from alembic import context
from sqlalchemy import create_engine, pool, text


def migrate(connection):
    if connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()!='0002':
        raise RuntimeError('Сначала требуется схема расчётного ядра 0002.')
    connection.commit()
    context.configure(connection=connection,version_table='web_alembic_version',transactional_ddl=True)
    with context.begin_transaction():context.run_migrations()


def run():
    if context.is_offline_mode():
        context.configure(url='postgresql+psycopg://',version_table='web_alembic_version',literal_binds=True,transactional_ddl=True)
        with context.begin_transaction():context.run_migrations()
        return
    supplied=context.config.attributes.get('connection')
    if supplied is not None:
        migrate(supplied)
        return
    url=os.environ.get('DATABASE_URL')
    if not url or not url.startswith('postgresql+psycopg://'):
        raise RuntimeError('Задайте DATABASE_URL мигратора PostgreSQL.')
    engine=create_engine(url,poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:migrate(connection)
    finally:engine.dispose()

run()
