"""Account/session security and project permissions against PostgreSQL."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import uuid

import pytest
pytest.importorskip('fastapi')
pytest.importorskip('argon2')
from fastapi.testclient import TestClient
from web_app.database import Database,grant_web_permissions
from web_app.main import create_app
from web_app.security import COOKIE,PH,normalize_email,validate_password,verify_password,token_hash

ROOT=Path(__file__).resolve().parents[1]
PASSWORD='Long student password 2026!'


def test_password_hash_and_wrong_password():
    encoded=PH.hash(PASSWORD)
    assert encoded.startswith('$argon2id$') and PASSWORD not in encoded
    assert verify_password(encoded,PASSWORD)
    assert not verify_password(encoded,'wrong password')
    assert not verify_password('!demo-disabled:invalid',PASSWORD)


@pytest.mark.parametrize('value',['short',' '*12,'x'*129])
def test_password_bounds(value):
    with pytest.raises(ValueError):validate_password(value)


def test_email_normalization():
    assert normalize_email(' Student@EXAMPLE.COM ')=='student@example.com'
    with pytest.raises(ValueError):normalize_email("x' OR true --")


@pytest.fixture(scope='module')
def web_env():
    if not os.environ.get('TEST_DATABASE_URL'):pytest.skip('Web integration requires TEST_DATABASE_URL')
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    url=os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    schema='web_test_'+uuid.uuid4().hex
    role='web_role_'+uuid.uuid4().hex
    admin=psycopg.connect(url,autocommit=True,row_factory=dict_row,prepare_threshold=None)
    admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    admin.execute(sql.SQL('CREATE ROLE {} LOGIN').format(sql.Identifier(role)))
    try:
        admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        admin.execute((ROOT/'db_migrations/versions/0001_initial.sql').read_text(encoding='utf-8'),prepare=False)
        admin.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
        admin.execute("INSERT INTO alembic_version VALUES('0002')")
        for name,path in [('ml_migration',ROOT/'db_migrations/versions/0002_model_registration.py'),('web_migration',ROOT/'web_migrations/versions/web0001_auth.py')]:
            spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            admin.execute(module.UP_SQL,prepare=False)
        admin.execute('CREATE TABLE web_alembic_version(version_num varchar(32) PRIMARY KEY)')
        admin.execute("INSERT INTO web_alembic_version VALUES('web0001')")
        admin.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(sql.Identifier(schema),sql.Identifier(role)))
        grant_web_permissions(admin,schema,role)
        class TestDatabase(Database):
            @contextmanager
            def connect(self):
                with psycopg.connect(url,row_factory=dict_row,prepare_threshold=None) as connection:
                    connection.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
                    connection.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
                    yield connection
        database=TestDatabase(url,expected_role=role)
        app=create_app(database)
        yield {'admin':admin,'app':app,'database':database}
    finally:
        admin.execute('SET search_path TO public')
        admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
        admin.close()


def client(web_env):
    return TestClient(web_env['app'],client=('192.0.2.'+str(int(uuid.uuid4().hex[:2],16)),50000))


def csrf(browser):
    response=browser.get('/api/auth/csrf')
    assert response.status_code==200
    return response.json()['csrf_token']


def signup(browser,name='Студент',email=None):
    email=email or f'student-{uuid.uuid4().hex}@example.com'
    response=browser.post('/api/auth/register',data={'name':name,'email':email,'password':PASSWORD,'csrf':csrf(browser)})
    assert response.status_code==201,response.text
    return response.json()['user']


def new_project(browser):
    response=browser.post('/api/projects',data={'title':'Выбор для офиса','goal':'Работа с таблицами','budget':'100000','csrf':csrf(browser)})
    assert response.status_code==201,response.text
    return response.json()['project_id']


def test_signup_rotation_logout_and_headers(web_env):
    with client(web_env) as browser:
        first_token=csrf(browser);old_cookie=browser.cookies.get(COOKIE)
        user=signup(browser)
        assert browser.cookies.get(COOKIE)!=old_cookie
        response=browser.get('/api/auth/me')
        assert response.json()['user']==user
        assert 'password' not in response.text and 'hash' not in response.text
        assert response.headers['cache-control']=='no-store'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
        session_cookie=browser.cookies.get(COOKIE)
        assert web_env['admin'].execute('SELECT count(*) AS n FROM web_sessions WHERE token_hash=%s',(token_hash(session_cookie),)).fetchone()['n']==1
        response=browser.post('/api/auth/logout',data={'csrf':csrf(browser)})
        assert response.status_code==200 and response.json()['logged_out']
        assert browser.get('/api/auth/me').status_code==401
        with client(web_env) as stolen:
            stolen.cookies.set(COOKIE,session_cookie)
            assert stolen.get('/api/auth/me').status_code==401
        rows=web_env['admin'].execute('SELECT password_hash FROM users WHERE id=%s',(user['id'],)).fetchone()
        assert rows['password_hash'].startswith('$argon2id$')
        assert web_env['admin'].execute('SELECT count(*) AS n FROM web_sessions WHERE token_hash=%s',(session_cookie,)).fetchone()['n']==0


def test_csrf_and_cross_origin_rejected(web_env):
    with client(web_env) as browser:
        token=csrf(browser)
        data={'name':'Студент','email':'csrf@example.com','password':PASSWORD}
        assert browser.post('/api/auth/register',data=data).status_code==403
        assert browser.post('/api/auth/register',data={**data,'csrf':'ошибка'}).status_code==403
        assert browser.post('/api/auth/register',data={**data,'csrf':token},headers={'origin':'https://other.example'}).status_code==403
        assert web_env['admin'].execute("SELECT count(*) AS n FROM users WHERE email='csrf@example.com'").fetchone()['n']==0


def test_project_scope_and_expert_cannot_grant_roles(web_env):
    with client(web_env) as analyst,client(web_env) as expert,client(web_env) as stranger:
        owner=signup(analyst);target=signup(expert);signup(stranger)
        pid=new_project(analyst)
        assert stranger.get(f'/api/projects/{pid}').status_code==404
        assert stranger.get('/api/projects').json()['projects']==[]
        response=analyst.post(f'/api/projects/{pid}/members',data={'email':target['email'],'role':'expert','csrf':csrf(analyst)})
        assert response.status_code==200,response.text
        assert expert.get(f'/api/projects/{pid}').json()['project']['role']=='expert'
        response=expert.post(f'/api/projects/{pid}/members',data={'email':target['email'],'role':'analyst','csrf':csrf(expert)})
        assert response.status_code==403
        assert analyst.post(f'/api/projects/{pid}/members',data={'email':owner['email'],'role':'expert','csrf':csrf(analyst)}).status_code==403
        assert len(analyst.get(f'/api/projects/{pid}').json()['criteria'])==5
        # Role change is checked from PostgreSQL, not cached in the user's session.
        analyst.post(f'/api/projects/{pid}/members',data={'email':target['email'],'role':'decision_maker','csrf':csrf(analyst)})
        assert expert.get(f'/api/projects/{pid}').json()['project']['role']=='decision_maker'


def test_inactive_account_revokes_current_session(web_env):
    with client(web_env) as browser:
        user=signup(browser)
        web_env['admin'].execute('UPDATE users SET is_active=false WHERE id=%s',(user['id'],))
        assert browser.get('/api/auth/me').status_code==401


def test_login_rate_limit_and_password_not_echoed(web_env):
    with client(web_env) as browser:
        user=signup(browser)
        browser.post('/api/auth/logout',data={'csrf':csrf(browser)})
        for index in range(5):
            response=browser.post('/api/auth/login',data={'email':user['email'],'password':'not-the-password','csrf':csrf(browser)})
            assert response.status_code==401,response.text
            assert 'not-the-password' not in response.text
        response=browser.post('/api/auth/login',data={'email':user['email'],'password':PASSWORD,'csrf':csrf(browser)})
        assert response.status_code==429


def test_xss_is_escaped_and_html_templates_render(web_env):
    name='<script>alert(1)</script>'
    with client(web_env) as browser:
        assert browser.get('/login').status_code==200
        assert browser.get('/register').status_code==200
        signup(browser,name=name)
        response=browser.get('/projects')
        assert response.status_code==200
        assert name not in response.text and '&lt;script&gt;' in response.text
        pid=new_project(browser)
        response=browser.get(f'/projects/{pid}')
        assert response.status_code==200
        assert 'Таблица решений' in response.text and 'Предоставить доступ' in response.text
        assert browser.get('/static/app.css').status_code==200


def test_database_role_cannot_change_schema_or_delete_users(web_env):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with web_env['database'].connect() as connection:
            connection.execute('CREATE TABLE forbidden(id integer)')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with web_env['database'].connect() as connection:
            connection.execute('DELETE FROM users')
    for statement in ['UPDATE users SET is_active=false','UPDATE projects SET owner_id=owner_id','DELETE FROM projects']:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with web_env['database'].connect() as connection:connection.execute(statement)


def test_invalid_budget_and_large_body_do_not_create_project(web_env):
    with client(web_env) as browser:
        signup(browser)
        assert browser.post('/api/projects',data={'title':'Проект','budget':'NaN','csrf':csrf(browser)}).status_code==422
        assert browser.post('/api/projects',content=b'x'*9000,headers={'content-type':'application/x-www-form-urlencoded'}).status_code==413
        assert browser.get('/api/projects').json()['projects']==[]


def test_successful_login_expiry_and_api_docs(web_env):
    with client(web_env) as browser:
        user=signup(browser)
        assert browser.post('/api/auth/logout',data={'csrf':csrf(browser)}).status_code==200
        assert browser.get('/openapi.json').status_code==401
        response=browser.post('/api/auth/login',data={'email':user['email'],'password':PASSWORD,'csrf':csrf(browser)})
        assert response.status_code==200,response.text
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
        assert browser.get('/docs').status_code==200
        assert '/api/projects' in browser.get('/openapi.json').json()['paths']
        token=browser.cookies.get(COOKIE)
        web_env['admin'].execute("UPDATE web_sessions SET created_at=now()-interval '2 minutes',expires_at=now()-interval '1 minute' WHERE token_hash=%s",(token_hash(token),))
        assert browser.get('/api/auth/me').status_code==401


def test_separate_alembic_stream_preserves_core():
    if not os.environ.get('TEST_DATABASE_URL'):pytest.skip('Migration integration requires TEST_DATABASE_URL')
    import psycopg
    from psycopg import sql
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine,text
    url=os.environ['TEST_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    schema='web_migration_'+uuid.uuid4().hex
    with psycopg.connect(url,autocommit=True,prepare_threshold=None) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            admin.execute((ROOT/'db_migrations/versions/0001_initial.sql').read_text(encoding='utf-8'),prepare=False)
            admin.execute('CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)')
            admin.execute("INSERT INTO alembic_version VALUES('0002')")
            engine=create_engine(url.replace('postgresql://','postgresql+psycopg://',1),connect_args={'prepare_threshold':None})
            try:
                with engine.connect() as connection:
                    connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit()
                    config=Config(str(ROOT/'web_alembic.ini'));config.attributes['connection']=connection
                    command.upgrade(config,'web0001');command.upgrade(config,'web0001')
                    assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0002'
                    assert connection.execute(text('SELECT version_num FROM web_alembic_version')).scalar_one()=='web0001'
                    connection.commit()
                    command.downgrade(config,'base')
                    assert connection.execute(text("SELECT to_regclass('web_sessions')")).scalar_one() is None
                    assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0002'
                    connection.commit();command.upgrade(config,'web0001')
                    assert connection.execute(text("SELECT to_regclass('web_sessions')")).scalar_one() is not None
                    connection.commit()
            finally:engine.dispose()
        finally:
            admin.execute('SET search_path TO public')
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
