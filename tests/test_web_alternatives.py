"""Transactions, scoped permissions, real ML artifacts and historical replay."""
import importlib.util
from pathlib import Path
import uuid
import pytest
pytest.importorskip('fastapi')
from test_web_auth import web_env,client,signup,csrf,new_project
from web_app.database import grant_web_permissions
from web_app.alternative_validation import validate_form,InvalidForm

ROOT=Path(__file__).resolve().parents[1]
BASE={'code':'X1','name':'Новый учебный ноутбук','description':'Для офисной работы',
 'cpu_cores':'4','cpu_threads':'8','cpu_frequency_ghz':'2.5','ram_gb':'8','storage_gb':'512','storage_type':'SSD',
 'price_rub':'50000','mass_kg':'1.5','battery_hours':'9','warranty_months':'24','performance_mode':'auto','change_reason':'Ввод по учебному предложению'}


@pytest.mark.parametrize('field,value',[
 ('price_rub',''),('price_rub','NaN'),('price_rub','Infinity'),('mass_kg','0'),('battery_hours','-1'),
 ('warranty_months','2.5'),('cpu_cores','4.5'),('cpu_threads','3'),('cpu_frequency_ghz','20'),
 ('storage_type','NVME'),('code','../x'),('name','a'),('ram_gb','1e1000')])
def test_invalid_fields_rejected(field,value):
    with pytest.raises(InvalidForm):validate_form({**BASE,field:value})


def test_decimal_comma_and_integer_validation():
    result=validate_form({**BASE,'mass_kg':'1,75'})
    assert result['scores']['mass_kg']==1.75 and result['features']['cpu_threads']==8


def test_web0002_online_upgrade_repeat_and_downgrade(web_env):
    import os
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine,text
    url=os.environ['TEST_DATABASE_URL'].replace('postgresql://','postgresql+psycopg://',1)
    schema=web_env['admin'].execute('SELECT current_schema() AS s').fetchone()['s']
    engine=create_engine(url,connect_args={'prepare_threshold':None})
    try:
        with engine.connect() as connection:
            connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit()
            config=Config(str(ROOT/'web_alembic.ini'));config.attributes['connection']=connection
            command.upgrade(config,'web0002');command.upgrade(config,'web0002')
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='0002'
            assert connection.execute(text('SELECT version_num FROM web_alembic_version')).scalar_one()=='web0002'
            connection.commit();command.downgrade(config,'web0001')
            assert connection.execute(text("SELECT to_regclass('web_deleted_alternatives')")).scalar_one() is None
            connection.commit()
    finally:engine.dispose()


@pytest.fixture(scope='module')
def edits(web_env):
    connection=web_env['admin']
    spec=importlib.util.spec_from_file_location('web_edit_migration',ROOT/'web_migrations/versions/web0002_alternative_archive.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    connection.execute(migration.UP_SQL,prepare=False)
    connection.execute("UPDATE web_alembic_version SET version_num='web0002'")
    grant_web_permissions(connection,connection.execute('SELECT current_schema() AS s').fetchone()['s'],web_env['database'].expected_role)
    from scripts.seed_demo import seed,load_payload
    from scripts.register_ml import register,load_bundle
    with connection.transaction():
        connection.execute("UPDATE alembic_version SET version_num='0001'")
        demo=seed(connection,load_payload(ROOT))
        connection.execute("UPDATE alembic_version SET version_num='0002'")
        register(connection,load_bundle(ROOT))
    return {**web_env,'demo_id':demo['project_id']}


def version(browser,pid):return browser.get(f'/api/projects/{pid}').json()['project']['revision']


def create(browser,pid,**changes):
    return browser.post(f'/api/projects/{pid}/alternatives',data={**BASE,'revision':str(version(browser,pid)),'csrf':csrf(browser),**changes})


def edit(browser,pid,aid,**changes):
    response=browser.get(f'/api/projects/{pid}/alternatives/{aid}')
    row=response.json() if response.status_code==200 else {'alternative':{'features':{}},'scores':[]}
    fields={**BASE,**row['alternative'],**row['alternative']['features'],**{r['code']:str(r['value']) for r in row['scores']}}
    fields={k:str(v) for k,v in fields.items() if k in BASE}
    return browser.post(f'/api/projects/{pid}/alternatives/{aid}',data={**fields,'revision':str(version(browser,pid)),'csrf':csrf(browser),**changes})


def analyst(edits):
    browser=client(edits);user=signup(browser)
    edits['admin'].execute("INSERT INTO project_members(project_id,user_id,role) VALUES(%s,%s,'analyst')",(edits['demo_id'],user['id']))
    return browser,user


def remove(browser,pid,aid,code='X1',**extra):
    return browser.post(f'/api/projects/{pid}/alternatives/{aid}/delete',data={'revision':str(version(browser,pid)),'csrf':csrf(browser),'confirm_code':code,'change_reason':'Альтернатива снята с рассмотрения',**extra})


def test_draft_without_model_and_validation_preserves_form(edits):
    with client(edits) as browser:
        signup(browser);pid=new_project(browser)
        response=create(browser,pid,mass_kg='bad')
        assert response.status_code==422 and 'mass_kg' in response.json()['errors']
        assert browser.get(f'/api/projects/{pid}').json()['alternatives']==[]
        response=create(browser,pid)
        assert response.status_code==201,response.text
        aid=response.json()['alternative_id']
        detail=browser.get(f'/api/projects/{pid}/alternatives/{aid}').json()
        assert len(detail['scores'])==4 and response.json()['prediction_id'] is None
        assert browser.get(f'/projects/{pid}/alternatives/new').status_code==200
        response=browser.post(f'/projects/{pid}/alternatives/{aid}',data={**BASE,'revision':str(version(browser,pid)),'csrf':csrf(browser),'price_rub':'bad'})
        assert response.status_code==422 and 'Новый учебный ноутбук' in response.text and 'value="bad"' in response.text


def test_ml_create_edit_history_override_and_restore(edits):
    browser,user=analyst(edits);pid=edits['demo_id']
    with browser:
        response=create(browser,pid)
        assert response.status_code==201,response.text
        aid=response.json()['alternative_id'];first=response.json()['prediction_id'];assert first
        row=browser.get(f'/api/projects/{pid}/alternatives/{aid}').json()
        perf=next(s for s in row['scores'] if s['code']=='performance_score')
        assert perf['source']=='ml' and perf['value']>0
        bad=edit(browser,pid,aid,performance_mode='override',performance_override='1200',change_reason='')
        assert bad.status_code==422
        response=edit(browser,pid,aid,performance_mode='override',performance_override='1200',change_reason='Учебная экспертная корректировка')
        assert response.status_code==200,response.text
        row=browser.get(f'/api/projects/{pid}/alternatives/{aid}').json()
        corrected=next(s for s in row['scores'] if s['code']=='performance_score')
        assert corrected['source']=='manual' and corrected['prediction_id']==first and corrected['value']==1200
        assert edits['admin'].execute('SELECT count(*) AS n FROM score_history WHERE project_id=%s AND score_id=%s',(pid,corrected['id'])).fetchone()['n']==1
        response=edit(browser,pid,aid,performance_mode='auto',cpu_cores='8',cpu_threads='16',cpu_frequency_ghz='3.0')
        assert response.status_code==200,response.text
        assert response.json()['prediction_id']!=first
        assert edits['admin'].execute('SELECT value FROM predictions WHERE id=%s',(first,)).fetchone()['value']==perf['value']
        assert browser.get(f'/projects/{pid}').status_code==200
        page=browser.get(f'/projects/{pid}/alternatives/{aid}/edit')
        assert page.status_code==200 and 'Последние изменения оценок' in page.text
        assert remove(browser,pid,aid).status_code==200


def test_expert_scoped_scores_and_lpr_read_only(edits):
    with client(edits) as owner,client(edits) as expert,client(edits) as lpr,client(edits) as outsider:
        signup(owner);e=signup(expert);l=signup(lpr);signup(outsider);pid=new_project(owner)
        for u,role in [(e,'expert'),(l,'decision_maker')]:
            assert owner.post(f'/api/projects/{pid}/members',data={'email':u['email'],'role':role,'csrf':csrf(owner)}).status_code==200
        aid=create(owner,pid).json()['alternative_id']
        assert create(expert,pid).status_code==403
        assert outsider.get(f'/api/projects/{pid}/alternatives/{aid}').status_code==404
        assert edit(lpr,pid,aid).status_code==403
        assert remove(expert,pid,aid).status_code==403
        payload={**{k:BASE[k] for k in ['price_rub','mass_kg','battery_hours','warranty_months']},'price_rub':'51000','change_reason':'Уточнена цена','csrf':csrf(expert),'revision':str(version(expert,pid))}
        path=f'/api/projects/{pid}/alternatives/{aid}/scores'
        assert expert.post(path,data={**payload,'cpu_cores':'8'}).status_code==422
        assert expert.post(path,data=payload).status_code==200
        score=next(s for s in owner.get(f'/api/projects/{pid}/alternatives/{aid}').json()['scores'] if s['code']=='price_rub')
        assert score['expert_id']==e['id'] and score['value']==51000
        assert expert.get(f'/projects/{pid}/alternatives/{aid}/scores').status_code==200
        assert expert.get(f'/projects/{pid}/alternatives/{aid}/edit').status_code==403
        assert lpr.get(f'/projects/{pid}').status_code==200


def test_csrf_conflicts_duplicate_codes_and_cross_project_ids(edits):
    with client(edits) as browser:
        signup(browser);pid=new_project(browser);other=new_project(browser)
        stale=version(browser,pid);aid=create(browser,pid).json()['alternative_id']
        assert create(browser,pid).status_code==422
        assert edit(browser,pid,aid,revision=str(stale)).status_code==409
        assert edit(browser,pid,aid,csrf='ошибка').status_code==403
        assert edit(browser,other,aid).status_code==404
        assert browser.get(f'/projects/{other}/alternatives/{aid}/delete').status_code==404
        assert remove(browser,pid,aid,code='WRONG').status_code==422
        assert browser.get(f'/api/projects/{pid}/alternatives/{aid}').status_code==200


def test_model_failure_rolls_back_new_alternative(edits):
    browser,user=analyst(edits);pid=edits['demo_id']
    with browser:
        model=edits['admin'].execute('SELECT id,artifact_path FROM ml_models WHERE project_id=%s AND is_active',(pid,)).fetchone()
        edits['admin'].execute('UPDATE ml_models SET artifact_path=%s WHERE id=%s',('../untrusted.joblib',model['id']))
        before=version(browser,pid)
        try:
            response=create(browser,pid,code='FAIL')
            assert response.status_code==422,response.text
            assert version(browser,pid)==before
            assert edits['admin'].execute("SELECT count(*) AS n FROM alternatives WHERE project_id=%s AND code='FAIL'",(pid,)).fetchone()['n']==0
        finally:edits['admin'].execute('UPDATE ml_models SET artifact_path=%s WHERE id=%s',(model['artifact_path'],model['id']))


def test_archive_keeps_predictions_and_replay_uses_historical_snapshot(edits):
    from decision_store.calculations import run_calculation,replay_result,core_version
    browser,user=analyst(edits);pid=edits['demo_id']
    with browser:
        with edits['admin'].transaction():calculated=run_calculation(edits['admin'],pid,.5)
        aid=edits['admin'].execute("SELECT id FROM alternatives WHERE project_id=%s AND code='A1'",(pid,)).fetchone()['id']
        # Make a correction so there is actual score history to preserve.
        assert edit(browser,pid,aid,price_rub='36000').status_code==200
        result=remove(browser,pid,aid,code='A1');assert result.status_code==200,result.text
        saved=browser.get(f"/api/projects/{pid}/deleted-alternatives/{result.json()['archive_id']}").json()
        assert saved['snapshot']['predictions'] and saved['snapshot']['score_history'] and len(saved['snapshot']['scores'])==5
        assert browser.get(f'/api/projects/{pid}/alternatives/{aid}').status_code==404
        assert browser.get(f'/projects/{pid}/deleted-alternatives').status_code==200
        assert browser.get(f'/api/projects/{pid}').json()['stale_results'] is True
        with edits['admin'].transaction():replayed=replay_result(edits['admin'],calculated['result_ids']['marcos'],pid)
        assert replayed['verified'] and replayed['is_stale']
        assert core_version()=='2615dd1e33d27cf817c78c9acfd1c5c39e05de3d8df4a5ab692c3edab6011729'
        # Archive is append-only for the server role.
        import psycopg
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with edits['database'].connect() as con:con.execute('DELETE FROM web_deleted_alternatives')


def test_fifteen_alternative_limit_and_noop_revision(edits):
    with client(edits) as browser:
        signup(browser);pid=new_project(browser)
        for i in range(15):
            response=create(browser,pid,code=f'L{i}')
            assert response.status_code==201,response.text
        assert create(browser,pid,code='L15').status_code==422
        aid=response.json()['alternative_id'];before=version(browser,pid)
        assert edit(browser,pid,aid,code='L14').status_code==200
        assert version(browser,pid)==before


def test_model_checksum_verified_before_deserialization(tmp_path,monkeypatch):
    from web_app.laptop_predictions import load_pipeline
    (tmp_path/'models').mkdir();(tmp_path/'models/ridge.joblib').write_bytes(b'not a trusted model')
    called=[]
    monkeypatch.setattr('web_app.laptop_predictions.joblib.load',lambda *args:called.append(True))
    with pytest.raises(ValueError,match='Контрольная сумма'):
        load_pipeline(str(tmp_path),'models/ridge.joblib','0'*64,'ridge')
    assert called==[]
    with pytest.raises(ValueError,match='Путь'):
        load_pipeline(str(tmp_path),'../untrusted.joblib','0'*64,'ridge')


def test_alternative_name_escaped(edits):
    name='<script>alert(1)</script>'
    with client(edits) as browser:
        signup(browser);pid=new_project(browser)
        response=create(browser,pid,name=name);assert response.status_code==201
        page=browser.get(f'/projects/{pid}')
        assert name not in page.text and '&lt;script&gt;' in page.text
