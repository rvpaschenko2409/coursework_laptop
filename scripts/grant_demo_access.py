"""Explicit local administrator action: add an active account to the demo project."""
import argparse
import getpass
import json
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from web_app.security import normalize_email


def grant_access(connection,email: str) -> dict:
    """Caller owns the transaction; preserve the existing project and snapshots."""
    email=normalize_email(email)
    project=connection.execute("SELECT id,owner_id FROM projects WHERE settings->>'demo_key'='laptop-variant27-demo-v1' FOR UPDATE").fetchall()
    if len(project)!=1:raise ValueError('Демонстрационный проект не найден однозначно.')
    user=connection.execute('SELECT id FROM users WHERE email=%s AND is_active',(email,)).fetchone()
    if not user:raise ValueError('Сначала зарегистрируйте этот email в веб-приложении.')
    pid=project[0]['id']
    old=connection.execute('SELECT role::text AS role FROM project_members WHERE project_id=%s AND user_id=%s',(pid,user['id'])).fetchone()
    changed=not old or old['role']!='analyst'
    if changed:
        connection.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'analyst') ON CONFLICT(project_id,user_id) DO UPDATE SET role='analyst'",(pid,user['id']))
        connection.execute('UPDATE projects SET settings=settings WHERE id=%s',(pid,))
        connection.execute("INSERT INTO audit_events(project_id,actor_id,action,entity_type,entity_id,details) VALUES(%s,%s,'grant_demo_access','user',%s,%s)",(pid,user['id'],str(user['id']),Jsonb({'database_role':'coursework_migrator','granted_role':'analyst','event_origin':'local_administrator_cli','actor_reference':'recipient_account'})))
    return {'project_id':pid,'user_id':user['id'],'role':'analyst','changed':changed}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email',required=True)
    parser.add_argument('--host',default='localhost')
    parser.add_argument('--port',type=int,default=5432)
    args=parser.parse_args();email=normalize_email(args.email)
    password=getpass.getpass('Пароль coursework_migrator: ')
    info=make_conninfo(host=args.host,port=args.port,dbname='coursework_laptop',user='coursework_migrator',password=password,connect_timeout=10)
    with psycopg.connect(info,row_factory=dict_row) as connection:result=grant_access(connection,email)
    print('Доступ к демонстрационному проекту предоставлен.' if result['changed'] else 'Доступ уже предоставлен; повторных изменений нет.')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except ValueError as exc:print(str(exc));raise SystemExit(1)
    except psycopg.Error:
        print('Доступ не изменён. Проверьте PostgreSQL, пароль coursework_migrator и установленные миграции.')
        raise SystemExit(1)
