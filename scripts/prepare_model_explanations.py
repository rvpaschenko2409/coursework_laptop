"""Apply web0004 and grant the existing non-owner application role explanation permissions."""
import argparse
import getpass
from pathlib import Path
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy import create_engine,URL
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config
from web_app.database import grant_model_explanation_permissions

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='localhost');parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args();password=getpass.getpass('Пароль coursework_migrator: ')
    engine=create_engine(URL.create('postgresql+psycopg',username='coursework_migrator',password=password,
        host=args.host,port=args.port,database='coursework_laptop'),hide_parameters=True)
    try:
        with engine.connect() as connection:
            config=Config(str(ROOT/'web_alembic.ini'));config.attributes['connection']=connection
            command.upgrade(config,'web0004')
    finally:engine.dispose()
    dsn=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(dsn,row_factory=dict_row) as connection:grant_model_explanation_permissions(connection)
    print('Веб-схема web0004 и права coursework_app для объяснения моделей подготовлены.')
    print('Ядро остаётся 0002. Существующие данные, модели и результаты не заменялись.')

if __name__=='__main__':
    try:main()
    except (ValueError,psycopg.Error,SQLAlchemyError):
        print('Подготовка не выполнена. Проверьте PostgreSQL, пароль coursework_migrator и версию схемы ядра 0002.');raise SystemExit(1)
