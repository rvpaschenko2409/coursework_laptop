"""Grouped ML evaluation, durable queue, real artifacts and explicit safe activation."""
from pathlib import Path
import importlib.util
import json
import shutil
import time
import uuid

import pandas as pd
import numpy as np
import pytest
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from web_app import model_training_math as math,model_training_store as store
from web_app import laptop_predictions
from web_app.database import grant_model_training_permissions
from web_app.model_training_worker import TrainingService
from test_web_auth import web_env,client,signup,csrf,new_project
from test_web_alternatives import edits,analyst,create as create_laptop
from web_app.training_data_store import inspect,save

ROOT=Path(__file__).resolve().parents[1]


def source_records(n=2000):
    data=pd.read_csv(ROOT/'data/synthetic/laptops_synthetic.csv').head(n)
    return [{'row_number':i+1,'data':row} for i,row in enumerate(data.to_dict('records'))]


def test_group_split_matches_baseline_and_folds_have_no_leakage():
    rows,frame,groups,train,test,cv,manifest=math.split(source_records(),42)
    assert len(train)==1608 and len(test)==392
    assert len(set(groups[train]))==80 and len(set(groups[test]))==20
    assert not set(groups[train])&set(groups[test])
    assert all(not set(groups[train][fit])&set(groups[train][valid]) for fit,valid in cv)
    assert len(manifest['assignments'])==2000 and manifest['group_overlap']==0
    assert manifest==math.split(source_records(),42)[-1]
    assert manifest['train_groups']!=math.split(source_records(),43)[-1]['train_groups']


@pytest.mark.parametrize('mode',['few','constant','missing','groups','nonfinite'])
def test_invalid_training_data(mode):
    rows=source_records(100)
    if mode=='few':rows=rows[:19]
    elif mode=='constant':
        for row in rows:row['data']['synthetic_score']=100
    elif mode=='missing':rows[0]['data']['ram_gb']=''
    elif mode=='nonfinite':rows[0]['data']['synthetic_score']='NaN'
    elif mode=='groups':
        for row in rows:row['data']={**rows[0]['data'],'synthetic_score':row['data']['synthetic_score']}
    with pytest.raises(ValueError):math.split(rows,42)


def test_real_training_selects_by_cv_then_evaluates_and_serializes():
    steps=[];result=math.train(source_records(120),42,steps.append)
    assert result['selected']==min(result['models'],key=lambda name:(result['models'][name]['metrics']['cv_mae'],name))
    assert steps[-1]=='Итоговая оценка на отложенном тесте'
    assert len(result['models']['ridge']['candidates'])==3 and len(result['models']['random_forest']['candidates'])==4
    for name,model in result['models'].items():
        assert list(model['pipeline'].feature_names_in_)==laptop_predictions.FEATURES
        assert model['metrics']['test_mae']>=0 and model['metrics']['test_rmse']>=0
        json.dumps(model['metrics'],allow_nan=False)
    assert result['models']['random_forest']['pipeline'].named_steps['model'].n_jobs==1


@pytest.fixture(scope='module')
def model_env(edits,tmp_path_factory):
    admin=edits['admin'];schema=admin.execute('SELECT current_schema() AS s').fetchone()['s']
    spec=importlib.util.spec_from_file_location('ml_web_migration',ROOT/'web_migrations/versions/web0003_model_training.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    import os
    from sqlalchemy import create_engine,text
    from alembic import command
    from alembic.config import Config
    engine=create_engine(os.environ['TEST_DATABASE_URL'].replace('postgresql://','postgresql+psycopg://',1),connect_args={'prepare_threshold':None})
    try:
        with engine.connect() as connection:
            connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit()
            config=Config(str(ROOT/'web_alembic.ini'));config.attributes['connection']=connection
            command.upgrade(config,'web0003');command.upgrade(config,'web0003')
    finally:engine.dispose()
    grant_model_training_permissions(admin,schema,edits['database'].expected_role)
    root=tmp_path_factory.mktemp('trained_models')
    shutil.copytree(ROOT/'models',root/'models')
    edits['app'].state.training_service.root=root
    old=laptop_predictions.ROOT
    from web_app import model_training_routes
    old_route=model_training_routes.ROOT
    laptop_predictions.ROOT=root;model_training_routes.ROOT=root
    yield {**edits,'artifact_root':root,'migration':migration}
    laptop_predictions.ROOT=old;model_training_routes.ROOT=old_route


def add_source(env,pid,actor,n=120):
    data=pd.read_csv(ROOT/'data/synthetic/laptops_synthetic.csv').head(n).to_csv(index=False).encode()
    r=inspect(data,'test_training.csv',{'name':'Учебный набор','origin':'synthetic','source_url':'','license':'Учебная синтетика',
        'seed':'42','generator_version':'test-fixture','reason':'Проверка обучения'})
    with env['admin'].transaction():did=save(env['admin'],pid,actor,r)['dataset_id']
    return did


def enqueue(browser,pid,did,key=None,**changes):
    return browser.post(f'/api/projects/{pid}/models/train',data={'csrf':csrf(browser),'revision':str(browser.get(f'/api/projects/{pid}').json()['project']['revision']),
        'dataset_id':str(did),'seed':'42','request_key':key or uuid.uuid4().hex,**changes})


def wait_run(browser,pid,rid):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        response=browser.get(f'/api/projects/{pid}/models');assert response.status_code==200,response.text
        run=next(r for r in response.json()['runs'] if r['id']==rid)
        if run['status'] not in ('queued','running'):return run
        time.sleep(.1)
    pytest.fail('Training did not finish within the test timeout')


def activate(browser,pid,mid,**changes):
    return browser.post(f'/api/projects/{pid}/models/{mid}/activate',data={'csrf':csrf(browser),
        'revision':str(browser.get(f'/api/projects/{pid}').json()['project']['revision']),**changes})


def test_real_background_run_artifacts_lineage_activation_and_future_prediction(model_env):
    env=model_env
    with client(env) as browser:
        user=signup(browser);pid=new_project(browser);did=add_source(env,pid,user['id']);key=uuid.uuid4().hex
        assert browser.get(f'/projects/{pid}/models').status_code==200
        response=enqueue(browser,pid,did,key);assert response.status_code==202,response.text
        rid=response.json()['run_id']
        # Navigation remains available while the CPU worker owns the fit.
        assert browser.get('/api/projects').status_code==200
        assert enqueue(browser,pid,did,key).json()['run_id']==rid
        run=wait_run(browser,pid,rid);assert run['status']=='succeeded',run
        output=run['output'];assert output['group_overlap']==0 and output['train_rows']+output['test_rows']==120
        models=browser.get(f'/api/projects/{pid}/models').json()['models']
        assert len(models)==2 and not any(m['is_active'] for m in models)
        assert env['admin'].execute('SELECT parent_dataset_id FROM datasets WHERE id=%s',(output['dataset_id'],)).fetchone()['parent_dataset_id']==did
        layers=env['admin'].execute('SELECT layer::text,count(*) AS n FROM dataset_rows WHERE dataset_id=%s GROUP BY layer',(output['dataset_id'],)).fetchall()
        assert layers==[{'layer':'processed','n':120}]
        registered=env['admin'].execute('SELECT * FROM ml_models WHERE project_id=%s',(pid,)).fetchall()
        for m in registered:
            path=env['artifact_root']/m['artifact_path'];assert path.is_file()
            assert len(m['split_manifest']['folds'])==5
            model=laptop_predictions.load_pipeline(str(env['artifact_root']),m['artifact_path'],m['artifact_sha256'],m['algorithm'])
            assert model is not None
        mid=output['recommended_model_id'];before=browser.get(f'/api/projects/{pid}').json()['project']['revision']
        assert activate(browser,pid,mid).status_code==200
        assert browser.get(f'/api/projects/{pid}').json()['project']['revision']>before
        made=create_laptop(browser,pid);assert made.status_code==201,made.text
        prediction=env['admin'].execute('SELECT model_id FROM predictions WHERE project_id=%s',(pid,)).fetchone()
        assert prediction['model_id']==mid
        other=next(m['id'] for m in models if m['id']!=mid)
        old_scores=[dict(r) for r in env['admin'].execute('SELECT * FROM scores WHERE project_id=%s ORDER BY id',(pid,)).fetchall()]
        assert activate(browser,pid,other).status_code==200
        assert [dict(r) for r in env['admin'].execute('SELECT * FROM scores WHERE project_id=%s ORDER BY id',(pid,)).fetchall()]==old_scores
        assert browser.get(f'/projects/{pid}/models').status_code==200


def test_failed_fit_has_durable_error_no_partial_models_and_restart_recovery(model_env,monkeypatch):
    env=model_env
    def fail(*args,**kwargs):raise ValueError('Контрольная ошибка обучения')
    with client(env) as browser:
        u=signup(browser);pid=new_project(browser);did=add_source(env,pid,u['id'])
        monkeypatch.setattr(math,'train',fail)
        response=enqueue(browser,pid,did);rid=response.json()['run_id'];run=wait_run(browser,pid,rid)
        assert run['status']=='failed' and run['error']=='Контрольная ошибка обучения'
        assert env['admin'].execute('SELECT count(*) AS n FROM ml_models WHERE project_id=%s',(pid,)).fetchone()['n']==0
        assert not (env['artifact_root']/'models/web'/uuid.UUID(rid).hex).exists()
        # Emulate a process that stopped after marking the durable run running.
        with env['admin'].transaction():
            recovered,_=store.create(env['admin'],pid,u['id'],did,'42',uuid.uuid4().hex)
            env['admin'].execute("UPDATE web_training_runs SET status='running',started_at=now() WHERE id=%s",(recovered,))
        env['app'].state.training_service.kick()
        run=wait_run(browser,pid,recovered)
        assert run['status']=='interrupted'


def test_roles_csrf_foreign_dataset_artifact_rejection_and_request_validation(model_env):
    env=model_env
    with client(env) as owner,client(env) as expert,client(env) as stranger:
        u=signup(owner);e=signup(expert);signup(stranger);pid=new_project(owner);did=add_source(env,pid,u['id'])
        owner.post(f'/api/projects/{pid}/members',data={'csrf':csrf(owner),'email':e['email'],'role':'expert'})
        assert expert.get(f'/api/projects/{pid}/models').status_code==200
        assert enqueue(expert,pid,did).status_code==403
        assert enqueue(owner,pid,did,csrf='bad').status_code==403
        assert stranger.get(f'/api/projects/{pid}/models').status_code==404
        foreign=new_project(owner)
        assert enqueue(owner,foreign,did).status_code==404
        assert enqueue(owner,pid,did,seed='-1').status_code==422
        assert activate(owner,pid,999999).status_code==404
        for path in ('../evil.joblib','models/web/evil/ridge.joblib','models/web/'+'a'*32+'/other.joblib'):
            with pytest.raises(ValueError):laptop_predictions.load_pipeline(str(env['artifact_root']),path,'0'*64,'ridge')


def test_queued_cancel_and_model_registration_failure_roll_back(model_env,monkeypatch):
    env=model_env
    with client(env) as browser:
        u=signup(browser);pid=new_project(browser);did=add_source(env,pid,u['id'])
        # Hold the same database-wide worker lease to keep a real queued job pending.
        admin=env['admin'];lease=admin.execute("SELECT hashtextextended(current_schema()||':web-model-worker',0) AS k").fetchone()['k']
        admin.execute('SELECT pg_advisory_lock(%s)',(lease,))
        try:
            response=enqueue(browser,pid,did);rid=response.json()['run_id']
            assert enqueue(browser,pid,did).status_code==409
            cancelled=browser.post(f'/api/projects/{pid}/models/runs/{rid}/cancel',data={'csrf':csrf(browser)})
            assert cancelled.status_code==200
        finally:admin.execute('SELECT pg_advisory_unlock(%s)',(lease,))
        env['app'].state.training_service.kick()
        assert wait_run(browser,pid,rid)['status']=='cancelled'
        original=store.persist
        def fail(connection,run,result,artifacts):
            original(connection,run,result,artifacts)
            raise ValueError('Ошибка после регистрации')
        monkeypatch.setattr(store,'persist',fail)
        response=enqueue(browser,pid,did);rid=response.json()['run_id'];run=wait_run(browser,pid,rid)
        assert run['status']=='failed'
        assert env['admin'].execute('SELECT count(*) AS n FROM ml_models WHERE project_id=%s',(pid,)).fetchone()['n']==0
        assert env['admin'].execute('SELECT count(*) AS n FROM datasets WHERE project_id=%s',(pid,)).fetchone()['n']==1
        assert not (env['artifact_root']/'models/web'/uuid.UUID(rid).hex).exists()


def test_migration_guard_model_role_and_run_identity_immutable(model_env):
    env=model_env
    assert env['admin'].execute('SELECT version_num FROM alembic_version').fetchone()['version_num']=='0002'
    assert env['admin'].execute('SELECT version_num FROM web_alembic_version').fetchone()['version_num']=='web0003'
    env['database'].verify()
    with env['database'].connect() as connection:
        assert not connection.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE') AS ok").fetchone()['ok']
        assert not connection.execute("SELECT has_column_privilege(current_user,'ml_models','artifact_path','UPDATE') AS ok").fetchone()['ok']
    row=env['admin'].execute('SELECT id FROM web_training_runs LIMIT 1').fetchone()
    if row:
        import psycopg
        with pytest.raises(psycopg.Error):
            with env['admin'].transaction():env['admin'].execute('UPDATE web_training_runs SET params=%s WHERE id=%s',(Jsonb({'seed':99}),row['id']))
        with pytest.raises(psycopg.Error):
            with env['admin'].transaction():env['admin'].execute(env['migration'].DOWN_SQL,prepare=False)


def test_compare_completed_runs_from_saved_outputs(model_env):
    with client(model_env) as browser:
        user = signup(browser)
        pid = new_project(browser)
        did = add_source(model_env, pid, user['id'])
        path = f'/projects/{pid}/models/compare'

        empty = browser.get(path)
        assert empty.status_code == 200
        assert 'нужны два успешно завершённых запуска' in empty.text

        run_ids = []
        for seed in ('42', '42', '43'):
            queued = enqueue(browser, pid, did, seed=seed)
            assert queued.status_code == 202, queued.text
            rid = queued.json()['run_id']
            assert wait_run(browser, pid, rid)['status'] == 'succeeded'
            run_ids.append(rid)

        first, second, different = run_ids
        both = browser.get(path, params={'first': first, 'second': second})
        assert both.status_code == 200, both.text
        assert 'одинаковым разбиением' in both.text
        assert 'random_forest' in both.text and 'ridge' in both.text
        assert '<th>Разница</th>' in both.text and '+0.0000' in both.text

        changed = browser.get(path, params={'first': first, 'second': different})
        assert changed.status_code == 200
        assert 'разные наборы или разбиения' in changed.text
        assert '<th>Разница</th>' not in changed.text

        assert browser.get(path, params={'first': first, 'second': first}).status_code == 422
        assert browser.get(path, params={'first': first}).status_code == 422
        other = new_project(browser)
        assert browser.get(f'/projects/{other}/models/compare',
                           params={'first': first, 'second': second}).status_code == 404
