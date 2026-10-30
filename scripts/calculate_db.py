"""Run MEREC/MARCOS/VIKOR from PostgreSQL, or verify a historical result."""
import argparse
import getpass
import json
import sys

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from decision_store.calculations import replay_result, run_calculation, validate_v


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-id',type=int,default=1)
    parser.add_argument('--v',type=float,default=0.5)
    parser.add_argument('--replay',type=int,metavar='RESULT_ID')
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args()
    if args.replay is None:
        validate_v(args.v)
    password=getpass.getpass('Пароль coursework_migrator: ')
    info=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(info,autocommit=True,row_factory=dict_row) as connection:
        if args.replay is not None:
            result=replay_result(connection,args.replay,args.project_id)
        else:
            for attempt in range(3):
                try:
                    with connection.transaction():
                        result=run_calculation(connection,args.project_id,args.v)
                    break
                except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
                    if attempt==2:
                        raise
                except psycopg.errors.UniqueViolation as exc:
                    if attempt==2 or exc.diag.constraint_name!='results_project_id_request_key_key':
                        raise
    if args.replay is not None:
        print('Сохранённый результат воспроизведён по историческому снимку.')
    else:
        print('Расчёт и снимки сохранены в PostgreSQL.' if result['created'] else 'Расчёт уже сохранён; дубликаты не созданы.')
    print(json.dumps(result,ensure_ascii=False))
    if args.replay is None:
        print('MARCOS — больше utility лучше; VIKOR — меньше Q лучше. Решение принимает ЛПР.')


if __name__=='__main__':
    try:
        main()
    except (ValueError,psycopg.Error) as exc:
        if isinstance(exc,psycopg.Error):
            print(f'Ошибка PostgreSQL ({exc.sqlstate or "подключение"}). Новый расчёт не сохранён.',file=sys.stderr)
            if exc.diag.message_primary:
                print(exc.diag.message_primary,file=sys.stderr)
        else:
            print(f'Ошибка: {exc}',file=sys.stderr)
        raise SystemExit(1)
