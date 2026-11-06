"""Scientific identities, saved-version provenance and authenticated explanation exports."""
import hashlib
from pathlib import Path
from io import BytesIO
import importlib.util

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from PIL import Image

from web_app import model_explanations_math as math,model_explanations_store as store
from web_app.model_explanations_charts import png
from web_app.laptop_predictions import load_pipeline
from web_app.database import grant_model_explanation_permissions
from test_web_auth import web_env,client,signup,csrf,new_project
from test_web_alternatives import edits,analyst,create as create_laptop,edit as edit_laptop,remove
from test_model_training import model_env,add_source,enqueue,wait_run,activate

ROOT=Path(__file__).resolve().parents[1]
FEATURES={'cpu_cores':4,'cpu_threads':8,'cpu_frequency_ghz':2.5,'ram_gb':8,'storage_gb':512,'storage_type':'SSD'}


def legacy_rows(part):
    return [{'row_number':i+1,'data':{**row,'performance_score':row['synthetic_score']}} for i,row in enumerate(pd.read_csv(ROOT/f'data/processed/{part}.csv').to_dict('records'))]


def legacy_pipeline(name):
    path=ROOT/f'models/{name}.joblib'
    return load_pipeline(str(ROOT),path.relative_to(ROOT).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest(),name)


@pytest.mark.parametrize('name',['ridge','random_forest'])
def test_shap_additivity_saved_prediction_and_six_original_features(name):
    pipeline=legacy_pipeline(name)
    expected=float(pipeline.predict(pd.DataFrame([FEATURES],columns=math.FEATURES))[0])
    result=math.local(pipeline,name,FEATURES,legacy_rows('train'),expected)
    assert abs(result['base_value']+sum(r['contribution'] for r in result['rows'])-expected)<1e-6
    assert result['additivity_error']<1e-6 and len(result['rows'])==6
    assert result['rows'][-1]['feature']=='storage_type'
    assert result['background_rows']==(100 if name=='ridge' else 0)
    with pytest.raises(ValueError,match='не воспроизводится'):
        math.local(pipeline,name,FEATURES,legacy_rows('train'),expected+10)


def test_permutation_matches_existing_cli_and_negative_values_are_retained():
    result=math.importance(legacy_pipeline('random_forest'),legacy_rows('test'))
    expected=pd.read_csv(ROOT/'results/feature_importance.csv').set_index('feature')
    assert result['test_rows_used']==392 and result['test_rows_total']==392 and not result['subsampled']
    for row in result['rows']:
        assert abs(row['mean']-expected.loc[row['feature'],'mae_increase_mean'])<1e-8
        assert abs(row['std']-expected.loc[row['feature'],'mae_increase_std'])<1e-8
        assert len(row['repeats'])==30
    assert any(row['mean']<0 for row in result['rows'])


def test_test_sampling_is_bounded_and_repeatable():
    rows=[{'row_number':i,'data':{}} for i in range(2000)]
    a=math.subset(rows,500);b=math.subset(rows,500)
    assert a==b and len(a)==500 and len({r['row_number'] for r in a})==500


def test_exportable_pngs_are_valid_and_complete():
    import json
    # Use actual local SHAP; permutation numbers are small deterministic display fixtures here.
    model=legacy_pipeline('random_forest');expected=float(model.predict(pd.DataFrame([FEATURES],columns=math.FEATURES))[0])
    local=math.local(model,'random_forest',FEATURES,legacy_rows('train'),expected)
    importance={'rows':[{'label':label,'mean':float(i-1),'std':.3} for i,label in enumerate(math.LABELS.values())],'repeats':30}
    for kind in ('importance','shap'):
        data=png({'importance':importance,'local':local},kind)
        with Image.open(BytesIO(data)) as image:assert image.format=='PNG' and image.size==(1400,756)


@pytest.fixture(scope='module')
def explanations(model_env):
    env=model_env;admin=env['admin'];schema=admin.execute('SELECT current_schema() AS s').fetchone()['s']
    import os
    from sqlalchemy import create_engine,text
    from alembic import command
    from alembic.config import Config
    engine=create_engine(os.environ['TEST_DATABASE_URL'].replace('postgresql://','postgresql+psycopg://',1),connect_args={'prepare_threshold':None})
    try:
        with engine.connect() as connection:
            connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit()
            config=Config(str(ROOT/'web_alembic.ini'));config.attributes['connection']=connection
            command.upgrade(config,'web0004');command.upgrade(config,'web0004')
    finally:engine.dispose()
    grant_model_explanation_permissions(admin,schema,env['database'].expected_role)
    return env


def calculate(browser,pid,predid,**changes):
    return browser.post(f'/api/projects/{pid}/explanations',data={'csrf':csrf(browser),'prediction_id':str(predid),**changes})


def prediction(env,pid,aid=None):
    rows=env['admin'].execute('SELECT * FROM predictions WHERE project_id=%s ORDER BY id DESC',(pid,)).fetchall()
    return next(row for row in rows if aid is None or row['alternative_id']==aid)


def test_legacy_saved_prediction_explanation_cache_images_access_and_snapshot(explanations,monkeypatch):
    env=explanations;browser,_=analyst(env);pid=env['demo_id']
    with browser:
        made=create_laptop(browser,pid,code='EX1',name='<script>Учебный ноутбук</script>');assert made.status_code==201,made.text
        pred=prediction(env,pid);predid=pred['id']
        assert browser.get(f'/projects/{pid}/explanations').status_code==200
        r=calculate(browser,pid,predid);assert r.status_code==201,r.text
        eid=r.json()['explanation_id']
        data=browser.get(f'/api/projects/{pid}/explanations/{eid}').json()
        assert data['model_id']==pred['model_id'] and abs(data['output']['local']['prediction']-pred['value'])<1e-6
        html=browser.get(f'/projects/{pid}/explanations/{eid}');assert html.status_code==200,html.text
        assert '<script>Учебный' not in html.text and '&lt;script&gt;' in html.text
        assert 'База + вклады' in html.text
        for kind in ('importance','shap'):
            result=browser.get(f'/projects/{pid}/explanations/{eid}/{kind}.png')
            assert result.status_code==200 and result.content.startswith(b'\x89PNG')
        assert browser.get(f'/projects/{pid}/explanations/{eid}/snapshot.json').json()['id']==eid
        def fail(*args,**kwargs):raise AssertionError('Cached explanation must not recalculate importance/SHAP')
        monkeypatch.setattr(math,'importance',fail)
        assert create_laptop(browser,pid,code='EX2').status_code==201
        other=prediction(env,pid)
        assert calculate(browser,pid,other['id']).status_code==201
        monkeypatch.setattr(math,'local',fail)
        assert calculate(browser,pid,predid).json()['explanation_id']==eid
        # History belongs to the saved prediction, independent of later active-model changes.
        alt_id=pred['alternative_id'];edit_laptop(browser,pid,alt_id,performance_mode='override',performance_override='1200',change_reason='Учебная ручная корректировка')
        html=browser.get(f'/projects/{pid}/explanations/{eid}')
        assert 'Ручная корректировка не раскладывается' in html.text and '1200.000000' in html.text
        assert browser.get(f'/projects/{pid}/alternatives/{alt_id}/explanation').status_code==200
        assert env['admin'].execute('SELECT value FROM predictions WHERE id=%s',(predid,)).fetchone()['value']==pred['value']
        stranger=client(env)
        with stranger:
            signup(stranger)
            for suffix in ('', '/importance.png','/snapshot.json'):
                assert stranger.get(f'/projects/{pid}/explanations/{eid}{suffix}').status_code==404


def test_web_trained_ridge_and_rf_local_explanations_and_current_model_independence(explanations):
    env=explanations
    with client(env) as browser:
        u=signup(browser);pid=new_project(browser);did=add_source(env,pid,u['id'])
        rid=enqueue(browser,pid,did).json()['run_id'];run=wait_run(browser,pid,rid);assert run['status']=='succeeded',run
        for name in ('ridge','random_forest'):
            mid=run['output']['models'][name]['model_id'];assert activate(browser,pid,mid).status_code==200
            assert create_laptop(browser,pid,code='E'+('R' if name=='ridge' else 'F')).status_code==201
            pred=prediction(env,pid);r=calculate(browser,pid,pred['id']);assert r.status_code==201,r.text
            eid=r.json()['explanation_id'];data=browser.get(f'/api/projects/{pid}/explanations/{eid}').json()
            assert data['model_id']==mid and data['output']['local']['additivity_error']<1e-6
            assert data['input_snapshot']['group_overlap']==0
            if name=='ridge':ridge_eid=eid;ridge_mid=mid
        old=browser.get(f'/api/projects/{pid}/explanations/{ridge_eid}').json()
        assert old['model_id']==ridge_mid and old['input_snapshot']['model']['algorithm']=='ridge'
        assert browser.get(f'/projects/{pid}').status_code==200


def test_csrf_foreign_prediction_and_bad_requests(explanations):
    env=explanations
    with client(env) as browser:
        u=signup(browser);pid=new_project(browser)
        pred=prediction(env,env['demo_id'])
        assert calculate(browser,pid,pred['id']).status_code==404
        assert calculate(browser,pid,pred['id'],csrf='bad').status_code==403
        assert calculate(browser,pid,'invalid').status_code==422
        assert browser.get(f'/projects/{pid}/explanations').status_code==200
        assert browser.get(f'/projects/{pid}/alternatives/999999/explanation').status_code==404
        assert browser.get(f'/api/projects/{pid}/explanations/999999').status_code==404


def test_history_survives_alternative_deletion_with_snapshot(explanations):
    env=explanations;browser,_=analyst(env);pid=env['demo_id']
    with browser:
        assert create_laptop(browser,pid,code='EXD').status_code==201
        pred=prediction(env,pid);eid=calculate(browser,pid,pred['id']).json()['explanation_id']
        response=remove(browser,pid,pred['alternative_id'],code='EXD');assert response.status_code==200,response.text
        data=browser.get(f'/api/projects/{pid}/explanations/{eid}').json()
        assert data['prediction_id'] is None and data['input_snapshot']['prediction']['id']==pred['id']
        assert browser.get(f'/projects/{pid}/explanations/{eid}').status_code==200


def test_full_test_metric_verification_and_rollback_on_additivity_failure(explanations,monkeypatch):
    env=explanations;browser,_=analyst(env);pid=env['demo_id']
    with browser:
        assert create_laptop(browser,pid,code='EXF').status_code==201
        pred=prediction(env,pid)
        def fail(*args,**kwargs):raise ValueError('Контрольная ошибка суммы вкладов')
        monkeypatch.setattr(math,'local',fail)
        response=calculate(browser,pid,pred['id']);assert response.status_code==409
        assert env['admin'].execute('SELECT count(*) AS n FROM web_model_explanations WHERE prediction_id=%s',(pred['id'],)).fetchone()['n']==0
        old=env['admin'].execute('SELECT metrics FROM ml_models WHERE id=%s',(pred['model_id'],)).fetchone()['metrics']
        from psycopg.types.json import Jsonb
        env['admin'].execute('UPDATE ml_models SET metrics=%s WHERE id=%s',(Jsonb({**old,'test_mae':999999}),pred['model_id']))
        try:
            response=calculate(browser,pid,pred['id']);assert response.status_code==409
            assert 'MAE' in response.json()['detail']
        finally:env['admin'].execute('UPDATE ml_models SET metrics=%s WHERE id=%s',(Jsonb(old),pred['model_id']))


def test_web0004_permissions_core_version_and_no_update_grant(explanations):
    env=explanations;env['database'].verify()
    assert env['admin'].execute('SELECT version_num FROM alembic_version').fetchone()['version_num']=='0002'
    assert env['admin'].execute('SELECT version_num FROM web_alembic_version').fetchone()['version_num']=='web0004'
    with env['database'].connect() as connection:
        assert not connection.execute("SELECT has_table_privilege(current_user,'web_model_explanations','UPDATE') AS ok").fetchone()['ok']
        assert not connection.execute("SELECT has_schema_privilege(current_user,current_schema(),'CREATE') AS ok").fetchone()['ok']
