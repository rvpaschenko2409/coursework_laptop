"""Database connections and explicit grants for the non-owner web role."""
from dataclasses import dataclass, field
import psycopg
from psycopg import sql
from psycopg.rows import dict_row


@dataclass
class Database:
    dsn: str=field(repr=False)
    expected_role: str='coursework_app'

    def connect(self):
        return psycopg.connect(self.dsn,row_factory=dict_row)

    def verify(self):
        with self.connect() as connection:
            role=connection.execute('''SELECT current_user AS name,r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolbypassrls
                FROM pg_roles r WHERE r.rolname=current_user''').fetchone()
            if role['name']!=self.expected_role or any(role[key] for key in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls')):
                raise ValueError('Веб-сервер должен работать от отдельной непривилегированной роли приложения.')
            if connection.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']!='0002':
                raise ValueError('Нужна схема ядра 0002.')
            if connection.execute('SELECT version_num FROM web_alembic_version').fetchone()['version_num']!='web0001':
                raise ValueError('Нужна миграция web0001.')
            if connection.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE') AS allowed").fetchone()['allowed']:
                raise ValueError('Роль приложения не должна иметь CREATE в рабочей схеме.')
            connection.execute('DELETE FROM web_sessions WHERE expires_at<=now()')
            connection.execute("DELETE FROM web_auth_attempts WHERE window_started_at<now()-interval '1 day'")


def grant_web_permissions(connection,schema: str='public',role: str='coursework_app') -> None:
    info=connection.execute('SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
    if not info or not info['rolcanlogin'] or any(info[key] for key in ('rolsuper','rolcreatedb','rolcreaterole','rolbypassrls')):
        raise ValueError('Сначала создайте coursework_app с Can login=Yes и без административных привилегий.')
    if connection.execute('SELECT has_schema_privilege(%s,%s,\'CREATE\') AS allowed',(role,schema)).fetchone()['allowed']:
        raise ValueError('У роли приложения есть CREATE в схеме. Уберите это право от имени владельца схемы.')
    if not connection.execute('SELECT has_schema_privilege(%s,%s,\'USAGE\') AS allowed',(role,schema)).fetchone()['allowed']:
        raise ValueError('Владелец схемы должен выдать coursework_app право USAGE.')
    def grant(privileges,table):
        connection.execute(sql.SQL('GRANT '+privileges+' ON TABLE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(table),sql.Identifier(role)))
    for table in ('alembic_version','web_alembic_version','alternatives','criteria','scores','predictions','ml_models','datasets','results','weight_sets','weights','stakeholders'):
        grant('SELECT',table)
    grant('SELECT','users')
    grant('INSERT(email,password_hash,name)','users')
    grant('UPDATE(password_hash)','users')
    grant('SELECT','projects')
    grant('INSERT(owner_id,title,goal,budget)','projects')
    grant('UPDATE(title,goal,budget,status,settings,catwoe,root_definition,updated_at)','projects')
    grant('SELECT, INSERT','criteria')
    grant('SELECT, INSERT','project_members')
    grant('UPDATE(role)','project_members')
    grant('SELECT, INSERT','audit_events')
    for table in ('web_sessions','web_auth_attempts'):
        grant('SELECT, INSERT, UPDATE, DELETE',table)
    for sequence in ('users_id_seq','projects_id_seq','criteria_id_seq','audit_events_id_seq'):
        connection.execute(sql.SQL('GRANT USAGE ON SEQUENCE {}.{} TO {}').format(sql.Identifier(schema),sql.Identifier(sequence),sql.Identifier(role)))
