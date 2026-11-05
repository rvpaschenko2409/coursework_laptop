"""Project lifecycle with PostgreSQL transactions and a restricted role."""
import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_web_auth import web_env, client, csrf, signup, new_project
from web_app.database import grant_project_management_permissions
from web_app.project_store import validate_fields, insert_row

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('budget', ['NaN', 'Infinity', '-1', '0', '1000000000001', 'bad'])
def test_invalid_budget(budget):
    with pytest.raises(HTTPException):
        validate_fields('Проект', '', budget)


def test_budget_decimal_comma():
    assert validate_fields(' Проект ', ' Цель ', '100,25') == {
        'title': 'Проект', 'goal': 'Цель', 'budget': Decimal('100.25')}


@pytest.fixture(scope='module')
def managed(web_env):
    admin = web_env['admin']
    spec = importlib.util.spec_from_file_location(
        'project_archive_migration', ROOT / 'web_migrations/versions/web0002_alternative_archive.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    admin.execute(migration.UP_SQL, prepare=False)
    admin.execute("UPDATE web_alembic_version SET version_num='web0002'")
    schema = admin.execute('SELECT current_schema() AS s').fetchone()['s']
    grant_project_management_permissions(admin, schema, web_env['database'].expected_role)
    return web_env


def revision(browser, pid):
    return browser.get(f'/api/projects/{pid}').json()['project']['revision']


def form(browser, pid, **fields):
    return {'csrf': csrf(browser), 'revision': str(revision(browser, pid)), **fields}


def test_edit_copy_delete_and_replay(managed):
    with client(managed) as browser:
        owner = signup(browser)
        pid = new_project(browser)
        for action in ('edit','copy','delete'):
            page = browser.get(f'/projects/{pid}/{action}')
            assert page.status_code == 200
            assert 'name="csrf"' in page.text
        edit = browser.post(f'/api/projects/{pid}/edit', data=form(
            browser, pid, title='Изменённый проект', goal='Проверка копирования', budget='90000'))
        assert edit.status_code == 200, edit.text
        old_revision = revision(browser, pid)
        data = form(browser, pid, title='Независимая копия', goal='Цель', budget='80000',
                    request_key='same-copy-request-12345')
        copied = browser.post(f'/api/projects/{pid}/copy', data=data)
        assert copied.status_code == 201, copied.text
        copy_id = copied.json()['project_id']
        assert copy_id != pid and revision(browser, pid) == old_revision
        repeated = browser.post(f'/api/projects/{pid}/copy', data=data)
        assert repeated.status_code == 200
        assert repeated.json() == {'project_id': copy_id, 'created': False}
        source_criteria = browser.get(f'/api/projects/{pid}').json()['criteria']
        copied_criteria = browser.get(f'/api/projects/{copy_id}').json()['criteria']
        assert {r['id'] for r in source_criteria}.isdisjoint(r['id'] for r in copied_criteria)
        assert [r['code'] for r in source_criteria] == [r['code'] for r in copied_criteria]
        invalid = browser.post(f'/api/projects/{copy_id}/delete',
                               data=form(browser, copy_id, confirmation='неверно'))
        assert invalid.status_code == 422
        deleted = browser.post(f'/api/projects/{copy_id}/delete',
                               data=form(browser, copy_id, confirmation='Независимая копия'))
        assert deleted.status_code == 200, deleted.text
        assert browser.get(f'/api/projects/{copy_id}').status_code == 404
        assert browser.get(f'/api/projects/{pid}').status_code == 200


def test_permissions_csrf_and_stale_form(managed):
    with client(managed) as owner, client(managed) as expert, client(managed) as stranger:
        signup(owner)
        target = signup(expert)
        signup(stranger)
        pid = new_project(owner)
        response = owner.post(f'/api/projects/{pid}/members',
                              data={'email': target['email'], 'role':'expert', 'csrf':csrf(owner)})
        assert response.status_code == 200
        for action in ('edit', 'copy', 'delete'):
            assert expert.get(f'/projects/{pid}/{action}').status_code == 403
            assert stranger.get(f'/projects/{pid}/{action}').status_code == 404
            assert expert.post(f'/api/projects/{pid}/{action}', data={
                'csrf':csrf(expert)}).status_code == 403
        assert owner.post(f'/api/projects/{pid}/edit', data={'title':'Название'}).status_code == 403
        stale = form(owner, pid, title='Прежняя форма', goal='', budget='')
        managed['admin'].execute('UPDATE projects SET goal=%s WHERE id=%s', ('Другая цель',pid))
        assert owner.post(f'/api/projects/{pid}/edit', data=stale).status_code == 409
        assert owner.post(f'/api/projects/{pid}/delete',
                          data={**stale,'confirmation':'Выбор для офиса'}).status_code == 409


def test_copy_registered_data_and_calculation(managed):
    from scripts.seed_demo import seed, load_payload
    admin = managed['admin']
    with admin.transaction():
        admin.execute("UPDATE alembic_version SET version_num='0001'")
        pid = seed(admin, load_payload(ROOT))['project_id']
        admin.execute("UPDATE alembic_version SET version_num='0002'")
    with client(managed) as browser:
        actor = signup(browser)
        admin.execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'analyst')",
                      (pid,actor['id']))
        ds = insert_row(admin, 'datasets', {
            'project_id':pid,'name':'Тестовый набор','origin':'synthetic','license':'Учебные данные',
            'sha256':'a'*64,'seed':42,'generator_version':'test-v1','row_count':1,'column_count':7,
            'uploaded_by':actor['id']})
        insert_row(admin,'dataset_groups', {'dataset_id':ds,'configuration_id':'config-1','split':'train'},False)
        insert_row(admin,'dataset_rows', {'dataset_id':ds,'layer':'processed','row_number':1,
                   'configuration_id':'config-1','split':'train','data':{'synthetic_score':1000}})
        child = insert_row(admin,'datasets', {
            'project_id':pid,'parent_dataset_id':ds,'name':'Очищенный набор','origin':'synthetic',
            'license':'Учебные данные','sha256':'b'*64,'seed':42,'generator_version':'test-v1',
            'row_count':1,'column_count':7,'uploaded_by':actor['id']})
        model = insert_row(admin,'ml_models', {
            'project_id':pid,'dataset_id':ds,'name':'forest','version':1,'algorithm':'random_forest',
            'feature_names':['cpu_cores'],'target_name':'synthetic_score','seed':42,
            'artifact_path':'models/random_forest.joblib','artifact_sha256':'c'*64,
            'is_active':True,'trained_at':None,'created_by':actor['id']})
        criterion = admin.execute("SELECT id FROM criteria WHERE project_id=%s AND code='performance_score'",
                                  (pid,)).fetchone()['id']
        alternatives = admin.execute('SELECT * FROM alternatives WHERE project_id=%s ORDER BY code',(pid,)).fetchall()
        for index, alternative in enumerate(alternatives):
            pred = insert_row(admin,'predictions', {
                'project_id':pid,'model_id':model,'alternative_id':alternative['id'],'criterion_id':criterion,
                'value':600+index*200,'features_snapshot':{'cpu_cores':alternative['features']['cpu_cores']},'created_by':actor['id']})
            insert_row(admin,'scores', {'project_id':pid,'alternative_id':alternative['id'],'criterion_id':criterion,
                       'value':600+index*200,'source':'ml','prediction_id':pred,'expert_id':actor['id']})
        payload = form(browser,pid,title='Копия с моделями',goal='Новый сценарий',budget='100000',
                       request_key='copy-populated-data-12345')
        copied = browser.post(f'/api/projects/{pid}/copy',data=payload)
        assert copied.status_code == 201, copied.text
        new_id = copied.json()['project_id']
        settings = admin.execute('SELECT settings FROM projects WHERE id=%s',(new_id,)).fetchone()['settings']
        assert 'demo_key' not in settings
        for table, count in [('scores',25),('comparisons',25)]:
            assert admin.execute(f'SELECT count(*) AS n FROM {table} WHERE project_id=%s',(new_id,)).fetchone()['n'] == count
        new_ds = admin.execute('SELECT * FROM datasets WHERE project_id=%s ORDER BY id',(new_id,)).fetchall()
        assert len(new_ds) == 2 and new_ds[1]['parent_dataset_id'] == new_ds[0]['id']
        assert new_ds[0]['id'] not in (ds,child)
        linked = admin.execute("""SELECT count(*) AS n FROM scores s
            JOIN predictions p ON p.project_id=s.project_id AND p.id=s.prediction_id
            JOIN ml_models m ON m.project_id=p.project_id AND m.id=p.model_id
            WHERE s.project_id=%s AND m.dataset_id=%s""",(new_id,new_ds[0]['id'])).fetchone()['n']
        assert linked == 5
        run = browser.post(f'/api/projects/{new_id}/calculations',
                           data=form(browser,new_id,v='0.5'))
        assert run.status_code == 201, run.text
        deleted = browser.post(f'/api/projects/{new_id}/delete',
                               data=form(browser,new_id,confirmation='Копия с моделями'))
        assert deleted.status_code == 200, deleted.text
        assert admin.execute('SELECT count(*) AS n FROM results WHERE project_id=%s',(new_id,)).fetchone()['n'] == 0
        assert admin.execute('SELECT count(*) AS n FROM scores WHERE project_id=%s',(pid,)).fetchone()['n'] == 25


def test_copy_rollback_on_partial_failure(managed, monkeypatch):
    from web_app import project_store
    with client(managed) as browser:
        signup(browser)
        pid = new_project(browser)
        original_insert = project_store.insert_row
        count_before = managed['admin'].execute('SELECT count(*) AS n FROM projects').fetchone()['n']

        def fail_while_copying(connection, table, values, returning=True):
            if table == 'criteria':
                raise RuntimeError('Simulated interrupted copy')
            return original_insert(connection, table, values, returning)

        monkeypatch.setattr(project_store, 'insert_row', fail_while_copying)
        with pytest.raises(RuntimeError, match='Simulated interrupted copy'):
            browser.post(f'/api/projects/{pid}/copy', data=form(
                browser, pid, title='Неудачная копия', goal='', budget='',
                request_key='rollback-copy-request-12345'))
        assert managed['admin'].execute('SELECT count(*) AS n FROM projects').fetchone()['n'] == count_before
        assert managed['admin'].execute(
            "SELECT count(*) AS n FROM audit_events WHERE project_id=%s AND action='web_project_copy'",
            (pid,)).fetchone()['n'] == 0
