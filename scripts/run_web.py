"""Launch the local server without writing database credentials to disk."""
import argparse
import getpass
import psycopg
from psycopg.conninfo import make_conninfo
import uvicorn
from web_app.database import Database
from web_app.main import create_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-host',default='localhost')
    parser.add_argument('--db-port',type=int,default=5432)
    parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    password=getpass.getpass('Пароль PostgreSQL-роли coursework_app: ')
    dsn=make_conninfo(host=args.db_host,port=args.db_port,dbname='coursework_laptop',user='coursework_app',password=password,connect_timeout=10)
    app=create_app(Database(dsn))
    print(f'Откройте http://127.0.0.1:{args.port} . Остановка сервера: Ctrl+C.',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=True)


if __name__=='__main__':
    try:main()
    except (ValueError,psycopg.Error) as exc:
        if isinstance(exc,ValueError):print(str(exc))
        else:print('Подключение к БД не выполнено. Проверьте роль coursework_app, пароль и подготовку веб-схемы.')
        raise SystemExit(1)
