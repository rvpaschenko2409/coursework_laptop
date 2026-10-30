"""Migrate only the web module, or grant its already-created database role."""
import argparse
import getpass
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy import URL
from alembic import command
from alembic.config import Config
from web_app.database import grant_web_permissions

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--migrate-only',action='store_true')
    group.add_argument('--grant-only',action='store_true')
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args()
    password=getpass.getpass('Пароль coursework_migrator: ')
    if args.migrate_only:
        previous=os.environ.get('DATABASE_URL')
        try:
            os.environ['DATABASE_URL']=URL.create('postgresql+psycopg',username='coursework_migrator',password=password,host=args.host,port=args.port,database='coursework_laptop').render_as_string(hide_password=False)
            command.upgrade(Config(str(ROOT/'web_alembic.ini')),'web0001')
        finally:
            if previous is None:os.environ.pop('DATABASE_URL',None)
            else:os.environ['DATABASE_URL']=previous
        print('Веб-миграция web0001 выполнена. Версия ядра остаётся 0002.')
    else:
        info=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
        with psycopg.connect(info,row_factory=dict_row) as connection:
            grant_web_permissions(connection)
        print('Права роли coursework_app настроены. Административные права не выдавались.')


if __name__=='__main__':
    try:main()
    except ValueError as exc:
        print(str(exc));raise SystemExit(1)
