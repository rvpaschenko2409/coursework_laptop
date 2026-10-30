"""Grant existing application role permissions for individual matrices and DEMATEL."""
import argparse
import getpass
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from web_app.database import grant_web_permissions


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='localhost');parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args();password=getpass.getpass('Пароль coursework_migrator: ')
    dsn=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(dsn,row_factory=dict_row) as connection:
        if connection.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']!='0002' or connection.execute('SELECT version_num FROM web_alembic_version').fetchone()['version_num']!='web0002':
            raise ValueError('Сначала нужны версии ядра 0002 и веб-модуля web0002.')
        grant_web_permissions(connection)
    print('Права coursework_app для DEMATEL настроены.')
    print('Версии схем остались 0002 и web0002. Данные, модели и прежние результаты не заменялись.')

if __name__=='__main__':
    try:main()
    except ValueError as exc:print(str(exc));raise SystemExit(1)
    except psycopg.Error:
        print('Подготовка не выполнена. Проверьте PostgreSQL, пароль coursework_migrator и предыдущие этапы.')
        raise SystemExit(1)
